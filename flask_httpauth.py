"""
flask_httpauth
==================

This module provides Basic and Digest HTTP authentication for Flask routes.

:copyright: (C) 2014 by Miguel Grinberg.
:license:   MIT, see LICENSE for more details.
"""

from functools import wraps
from hashlib import md5
from random import Random, SystemRandom
from flask import request, make_response, session
from werkzeug.datastructures import Authorization
from werkzeug.security import safe_str_cmp

__version__ = '3.3.1dev'


class HTTPAuth(object):
    def __init__(self, scheme=None, realm=None):
        self.scheme = scheme
        self.realm = realm or "Authentication Required"
        self.get_password_callback = None
        self.auth_error_callback = None

        def default_get_password(username):
            return None

        def default_auth_error():
            return "Unauthorized Access"

        self.get_password(default_get_password)
        self.error_handler(default_auth_error)

    def get_password(self, f):
        self.get_password_callback = f
        return f

    def error_handler(self, f):
        @wraps(f)
        def decorated(*args, **kwargs):
            res = f(*args, **kwargs)
            res = make_response(res)
            if res.status_code == 200:
                # if user didn't set status code, use 401
                res.status_code = 401
            if 'WWW-Authenticate' not in res.headers.keys():
                res.headers['WWW-Authenticate'] = self.authenticate_header()
            return res
        self.auth_error_callback = decorated
        return decorated

    def authenticate_header(self):
        return '{0} realm="{1}"'.format(self.scheme, self.realm)

    def get_auth(self):
        auth = request.authorization
        if auth is None and 'Authorization' in request.headers:
            # Flask/Werkzeug do not recognize any authentication types
            # other than Basic or Digest, so here we parse the header by
            # hand
            try:
                auth_type, token = request.headers['Authorization'].split(
                    None, 1)
                auth = Authorization(auth_type, {'token': token})
            except ValueError:
                # The Authorization header is either empty or has no token
                pass

        # if the auth type does not match, we act as if there is no auth
        # this is better than failing directly, as it allows the callback
        # to handle special cases, like supporting multiple auth types
        if auth is not None and auth.type.lower() != self.scheme.lower():
            auth = None

        return auth

    def get_auth_password(self, auth):
        password = None

        if auth and auth.username:
            password = self.get_password_callback(auth.username)

        return password

    def login_required(self, f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = self.get_auth()

            # Flask normally handles OPTIONS requests on its own, but in the
            # case it is configured to forward those to the application, we
            # need to ignore authentication headers and let the request through
            # to avoid unwanted interactions with CORS.
            if request.method != 'OPTIONS':  # pragma: no cover
                password = self.get_auth_password(auth)

                if not self.authenticate(auth, password):
                    # Clear TCP receive buffer of any pending data
                    request.data
                    return self.auth_error_callback()

            return f(*args, **kwargs)
        return decorated

    def username(self):
        if not request.authorization:
            return ""
        return request.authorization.username


class HTTPBasicAuth(HTTPAuth):
    def __init__(self, scheme=None, realm=None):
        super(HTTPBasicAuth, self).__init__(scheme or 'Basic', realm)

        self.hash_password_callback = None
        self.verify_password_callback = None

    def hash_password(self, f):
        self.hash_password_callback = f
        return f

    def verify_password(self, f):
        self.verify_password_callback = f
        return f

    def authenticate(self, auth, stored_password):
        if auth:
            username = auth.username
            client_password = auth.password
        else:
            username = ""
            client_password = ""
        if self.verify_password_callback:
            return self.verify_password_callback(username, client_password)
        if not auth:
            return False
        if self.hash_password_callback:
            try:
                client_password = self.hash_password_callback(client_password)
            except TypeError:
                client_password = self.hash_password_callback(username,
                                                              client_password)
        return client_password is not None and \
            stored_password is not None and \
            safe_str_cmp(client_password, stored_password)


class HTTPDigestAuth(HTTPAuth):
    def __init__(self, scheme=None, realm=None, use_ha1_pw=False):
        super(HTTPDigestAuth, self).__init__(scheme or 'Digest', realm)
        self.use_ha1_pw = use_ha1_pw
        self.random = SystemRandom()
        try:
            self.random.random()
        except NotImplementedError:  # pragma: no cover
            self.random = Random()

        self.generate_nonce_callback = None
        self.verify_nonce_callback = None
        self.generate_opaque_callback = None
        self.verify_opaque_callback = None

        def _generate_random():
            return md5(str(self.random.random()).encode('utf-8')).hexdigest()

        def default_generate_nonce():
            session["auth_nonce"] = _generate_random()
            return session["auth_nonce"]

        def default_verify_nonce(nonce):
            session_nonce = session.get("auth_nonce")
            if nonce is None or session_nonce is None:
                return False
            return safe_str_cmp(nonce, session_nonce)

        def default_generate_opaque():
            session["auth_opaque"] = _generate_random()
            return session["auth_opaque"]

        def default_verify_opaque(opaque):
            session_opaque = session.get("auth_opaque")
            if opaque is None or session_opaque is None:
                return False
            return safe_str_cmp(opaque, session_opaque)

        self.generate_nonce(default_generate_nonce)
        self.generate_opaque(default_generate_opaque)
        self.verify_nonce(default_verify_nonce)
        self.verify_opaque(default_verify_opaque)

    def generate_nonce(self, f):
        self.generate_nonce_callback = f
        return f

    def verify_nonce(self, f):
        self.verify_nonce_callback = f
        return f

    def generate_opaque(self, f):
        self.generate_opaque_callback = f
        return f

    def verify_opaque(self, f):
        self.verify_opaque_callback = f
        return f

    def get_nonce(self):
        return self.generate_nonce_callback()

    def get_opaque(self):
        return self.generate_opaque_callback()

    def generate_ha1(self, username, password):
        a1 = username + ":" + self.realm + ":" + password
        a1 = a1.encode('utf-8')
        return md5(a1).hexdigest()

    def authenticate_header(self):
        nonce = self.get_nonce()
        opaque = self.get_opaque()
        return '{0} realm="{1}",nonce="{2}",opaque="{3}"'.format(
            self.scheme, self.realm, nonce,
            opaque)

    def authenticate(self, auth, stored_password_or_ha1):
        if not auth or not auth.username or not auth.realm or not auth.uri \
                or not auth.nonce or not auth.response \
                or not stored_password_or_ha1:
            return False
        if not(self.verify_nonce_callback(auth.nonce)) or \
                not(self.verify_opaque_callback(auth.opaque)):
            return False
        if self.use_ha1_pw:
            ha1 = stored_password_or_ha1
        else:
            a1 = auth.username + ":" + auth.realm + ":" + \
                stored_password_or_ha1
            ha1 = md5(a1.encode('utf-8')).hexdigest()
        a2 = request.method + ":" + auth.uri
        ha2 = md5(a2.encode('utf-8')).hexdigest()
        a3 = ha1 + ":" + auth.nonce + ":" + ha2
        response = md5(a3.encode('utf-8')).hexdigest()
        return safe_str_cmp(response, auth.response)


class HTTPTokenAuth(HTTPAuth):
    def __init__(self, scheme='Bearer', realm=None):
        super(HTTPTokenAuth, self).__init__(scheme, realm)

        self.verify_token_callback = None

    def verify_token(self, f):
        self.verify_token_callback = f
        return f

    def authenticate(self, auth, stored_password):
        if auth:
            token = auth['token']
        else:
            token = ""
        if self.verify_token_callback:
            return self.verify_token_callback(token)
        return False


class MultiAuth(object):
    def __init__(self, main_auth, *args):
        self.main_auth = main_auth
        self.additional_auth = args

    @property
    def selected_auth(self):
        selected_auth = None
        if 'Authorization' in request.headers:
            try:
                scheme, creds = request.headers['Authorization'].split(
                    None, 1)
            except ValueError:
                # malformed Authorization header
                pass
            else:
                for auth in self.additional_auth:
                    if auth.scheme == scheme:
                        selected_auth = auth
                        break
        if selected_auth is None:
            selected_auth = self.main_auth
        return selected_auth

    def login_required(self, f):
        @wraps(f)
        def decorated(*args, **kwargs):
            return self.selected_auth.login_required(f)(*args, **kwargs)
        return decorated


class HTTPRoleAuthMixin(object):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_auth_roles_callback = None

    def get_auth_roles(self, f):
        """ auth roles are the roles corresponding to the user/token in auth """
        self.get_auth_roles_callback = f
        return f

    def authorize(self, auth, endpoint_roles, use_all):
        if not auth:
            return False
        auth_roles = self.get_auth_roles_callback(auth)
        if use_all:
            return all(role in endpoint_roles for role in auth_roles)
        return any(role in endpoint_roles for role in auth_roles)

    def login_required(self, func=None, roles=None, use_all=False):
        """ endpoint roles are the roles (str) the user has to have to get access to the (decorated) endpoint """
        if func:  # in case 'login_required' is used without parentheses
            return super().login_required(func)

        def verify(auth, stored_password):
            return self.authenticate(auth, stored_password) and self.authorize(auth, roles, use_all)

        def decorator(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                """ basically the login_required decorated but with a check of 'authorize' """
                auth = self.get_auth()
                if request.method != 'OPTIONS':  # pragma: no cover
                    password = self.get_auth_password(auth)

                    if not verify(auth, password):
                        request.data  # empty the stream
                        return self.auth_error_callback()

                return f(*args, **kwargs)
            return decorated
        return decorator


class HTTPBasicRoleAuth(HTTPRoleAuthMixin, HTTPBasicAuth):
    pass


class HTTPTokenRoleAuth(HTTPRoleAuthMixin, HTTPTokenAuth):
    pass


class HTTPDigestRoleAuth(HTTPRoleAuthMixin, HTTPDigestAuth):
    pass


class MultiRoleAuth(MultiAuth):
    def __init__(self, main_auth, *args):
        for auth in (main_auth, *args):
            if not isinstance(auth, HTTPRoleAuthMixin):
                raise TypeError(f"cannot initialize '{self.__class__.__name__}' with '{auth.__class__.__name__}': "
                                f"because it does not inherit from 'HTTPRoleAuthMixin'")
        super().__init__(main_auth, *args)

    def login_required(self, func=None, roles=None, use_all=False):
        if func:
            return super().login_required(func)

        def decorator(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                return self.selected_auth.login_required(roles=roles, use_all=use_all)(f)(*args, **kwargs)
            return decorated
        return decorator


