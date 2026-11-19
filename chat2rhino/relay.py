# -*- coding: utf-8 -*-
"""The Chat2Rhino relay over HTTPS."""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from . import settings


class RelayError(Exception):
    def __init__(self, message, status=None, body=None):
        Exception.__init__(self, message)
        self.status = status
        self.body = body if isinstance(body, dict) else {}

    @property
    def token_refused(self):
        """The relay itself refused this computer's token (not a proxy's 403)."""
        return self.status == 403 and self.body.get("error") == "forbidden"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The token goes to the configured relay only: a redirect is an error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Relay(object):
    def __init__(self, url=None, token=""):
        self.url = (url or settings.RELAY_URL).rstrip("/")
        self.token = token

    def request(self, method, path, payload=None, query=None, timeout=None):
        """``(status, body)``; raises RelayError when the relay cannot be reached."""
        url = self.url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=timeout or settings.RELAY_TIMEOUT) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read())
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise RelayError(str(error))

    # -- the connector's calls --

    def pull(self, worker_id):
        status, body = self.request("GET", "/api/v1/pull",
                                    query={"worker_id": worker_id, "software": "rhino"})
        if status == 204:
            return None
        return _expect(status, body, "pull").get("job")

    def complete(self, job_id, worker_id, result):
        status, body = self.request(
            "POST", "/api/v1/jobs/{0}/complete".format(urllib.parse.quote(job_id, safe="")),
            {"worker_id": worker_id, "ok": bool(result.get("ok")), "result": result,
             "error": result.get("error")})
        _expect(status, body, "complete")

    def beat(self, payload):
        status, body = self.request("POST", "/api/v1/connector/state", payload, timeout=15)
        return _expect(status, body, "state")


def _expect(status, body, what):
    if status != 200:
        raise RelayError("{0} answered {1}".format(what, status), status, body)
    return body


def _decode(raw):
    text = raw.decode("utf-8", "replace") if raw else ""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {"text": text[:200]}
    return data if isinstance(data, dict) else {"value": data}
