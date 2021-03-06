# -*- coding: utf-8 -*-

from __future__ import absolute_import
import json
import logging
import sys
from time import sleep
from Crypto.Hash import SHA256

from cached_property import cached_property
from standardjson import StandardJSONEncoder
import requests
from six import string_types


if not logging.getLogger().getEffectiveLevel():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class Senderable(object):

    def __init__(self, wrapped, dkwargs, hash_value, attempts, *args, **kwargs):
        """
            :wrapped: Function that has been wrapped and whose payload is being sent
        """
        self.wrapped = wrapped
        self.dkwargs = dkwargs
        self.hash_value = hash_value
        self.attempts = attempts
        self.args = args
        self.kwargs = kwargs
        self.attempt = 0
        self.error = None
        self.response = None

    @staticmethod
    def _default_timeout():
        return 5

    @cached_property
    def url(self):
        return self.get_url()

    def get_url(self):
        return _value_in('url', True, kwargs=self.kwargs, dkwargs=self.dkwargs)

    @cached_property
    def custom_headers(self):
        return self.get_custom_headers()

    def get_custom_headers(self):
        return _value_in('custom_headers', False, kwargs=self.kwargs, dkwargs=self.dkwargs)

    @cached_property
    def signing_secret(self):
        return self.get_signing_secret()

    def get_signing_secret(self):
        return _value_in('signing_secret', False, kwargs=self.kwargs, dkwargs=self.dkwargs)

    @cached_property
    def encoding(self):
        return self.get_encoding()

    def get_encoding(self):
        encoding = _value_in('encoding', True, kwargs=self.kwargs, dkwargs=self.dkwargs)
        if not encoding or not (encoding == EncodingType.JSON or encoding == EncodingType.FORMS):
            msg = "Invalid choice for 'encoding'. Valid selections are '%s' or '%s'" % (EncodingType.FORMS, EncodingType.JSON)
            raise TypeError(msg)
        return encoding

    @cached_property
    def timeout(self):
        return self.get_timeout()

    def get_timeout(self):
        return _value_in('timeout', False, kwargs=self.kwargs, dkwargs=self.dkwargs) or self._default_timeout()

    @cached_property
    def payload(self):
        return self.get_payload()

    def get_payload(self):

        # Create the payload by calling the hooked/wrapped function.
        payload = self.wrapped(*self.args, **self.kwargs)

        return payload

    def format_payload(self):
        if self.get_encoding() == EncodingType.JSON:
            return self.jsonify_payload()
        return self.payload

    def jsonify_payload(self):
        """ Dump the payload to JSON """
        # Assume already json serialized
        if isinstance(self.payload, string_types):
            return self.payload
        return json.dumps(self.payload, cls=StandardJSONEncoder)

    def notify_debug(self, message):
        logging.debug(message)

    def notify(self, message):
        logging.info(message)

    def notify_error(self, message):
        logging.error(message)

    def send(self):
        """ Wrapper around _send method for use with asynchronous coding. """
        return self._send()

    def _send(self):
        """ Send the webhook method """

        payload = self.payload
        sending_metadata = {'success': False}
        post_attributes = {'timeout': self.timeout}

        if self.custom_headers:
            post_attributes['headers'] = self.custom_headers
        if not post_attributes.get('headers', None):
            post_attributes['headers'] = {}
        post_attributes['headers']['Content-Type'] = self.encoding

        post_attributes['data'] = self.format_payload()

        if self.signing_secret:
            post_attributes['headers']['x-hub-signature'] = self.create_signature(post_attributes['data'], \
                                                                                  self.signing_secret)

        for i, wait in enumerate(range(len(self.attempts) - 1)):

            self.attempt = i + 1
            sending_metadata['attempt'] = self.attempt

            try:
                print(self.url)
                self.response = requests.post(self.url, **post_attributes)

                if sys.version > '3':
                    # Converts bytes object to str object in Python 3+
                    self.response_content = self.response.content.decode('utf-8')
                else:
                    self.response_content = self.response.content

                sending_metadata['status_code'] = self.response.status_code

                # anything with a 200 status code  is a success
                if self.response.status_code >= 200 and self.response.status_code < 300:
                    # Exit the sender method.  Here we provide the payload as a result.
                    #   This is useful for reporting.
                    self.notify("Attempt {}: Successfully sent webhook {}".format(
                        self.attempt, self.hash_value)
                    )
                    sending_metadata['response'] = self.response_content
                    sending_metadata['success'] = True
                    break
                else:
                    self.error = "Status code (%d). Message: %s" % (self.response.status_code, self.response.text)


            except Exception as ex:
                err_formatted = str(ex).replace('"',"'")
                sending_metadata['response'] = '{"status_code": 500, "status":"failure","error":"'+err_formatted+'"}'
                self.error = err_formatted

            self.notify("Attempt {}: Could not send webhook {}".format(
                    self.attempt, self.hash_value)
            )
            self.notify_debug("Webhook {}. Body: {}".format(
                    self.hash_value, self.payload)
            )

            # If last attempt
            if self.attempt == (len(self.attempts) - 1):
                self.notify_error("Failed to send webhook {}. Body: {}".format(
                    self.hash_value, self.payload)
                )
            else:
                # Wait a bit before the next attempt
                sleep(wait)

        sending_metadata['error'] = None if sending_metadata['success'] or not self.error else self.error
        sending_metadata['post_attributes'] = post_attributes
        merged_dict = sending_metadata.copy()
        if isinstance(payload, string_types):
            payload = {'payload': payload}

        # Add the hash value if there is one.
        if self.hash_value is not None and len(self.hash_value) > 0:
            payload['hash'] = self.hash_value

        merged_dict.update(payload)
        return merged_dict

    def create_signature(self, payload, secret):
        if not isinstance(secret,bytes):
            secret = secret.encode('utf-8')
        if not isinstance(payload,string_types):
            # Data will be forms encoded
            payload = requests.PreparedRequest()._encode_params(payload)
        hmac = SHA256.new(secret)
        if not isinstance(payload,bytes):
            payload = payload.encode('utf-8')
        hmac.update(payload)
        return 'sha256=' + hmac.hexdigest()

def _value_in(key, required, dkwargs, kwargs):
    if key in kwargs:
        return kwargs[key]
    elif key in dkwargs:
        return dkwargs[key]
    elif required:
        msg = "Sender function needs a %s argument" % key
        raise TypeError(msg)
    return None

def value_in(key, dkwargs, kwargs):
    return _value_in(key, True, dkwargs, kwargs)

def value_in_opt(key, dkwargs, kwargs):
    return _value_in(key, False, dkwargs, kwargs)


class EncodingType(object):
    FORMS = 'application/x-www-form-urlencoded'
    JSON = 'application/json'