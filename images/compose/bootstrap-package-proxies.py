#!/usr/bin/env python3
"""Create npm/PyPI transparent caches; reruns reuse matching configuration.

PULP_URL, PULP_USERNAME, PULP_PASSWORD configure the management connection.
PULP_TASK_TIMEOUT bounds asynchronous task waits (default: 600 seconds).
"""

import base64
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


class Pulp:
    def __init__(self):
        self.url = os.getenv("PULP_URL", "http://localhost:8080").rstrip("/")
        credentials = "{}:{}".format(
            os.getenv("PULP_USERNAME", "admin"), os.getenv("PULP_PASSWORD", "password")
        )
        self.auth = "Basic " + base64.b64encode(credentials.encode()).decode()
        self.timeout = int(os.getenv("PULP_TASK_TIMEOUT", "600"))
        if self.timeout <= 0:
            raise ValueError("PULP_TASK_TIMEOUT must be positive")

    def request(self, method, path, data=None):
        url = urljoin(self.url + "/", path)
        if urlsplit(url)[:2] != urlsplit(self.url)[:2]:
            raise RuntimeError("Refusing an API URL outside PULP_URL")
        payload = None if data is None else json.dumps(data).encode()
        request = Request(url, data=payload, method=method, headers={
            "Authorization": self.auth, "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"{method} {path}: {error.reason}") from error

    def wait(self, response):
        if "task" not in response:
            return
        task_href = response["task"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            task = self.request("GET", task_href)
            if task["state"] == "completed":
                return
            if task["state"] in {"failed", "canceled", "skipped"}:
                raise RuntimeError(f"Task {task_href}: {task['state']}: {task.get('error')}")
            time.sleep(1)
        raise RuntimeError(f"Task {task_href} still running after {self.timeout}s; inspect it before retrying")

    def ensure(self, collection, fields):
        path = "/pulp/api/v3/" + collection + "/"
        query = path + "?" + urlencode({"name": fields["name"]})
        result = self.request("GET", query)
        if not result["count"]:
            self.wait(self.request("POST", path, fields))
            result = self.request("GET", query)
            print(f"Created {collection}: {fields['name']}")
        else:
            print(f"Reusing {collection}: {fields['name']}")
        if result["count"] != 1:
            raise RuntimeError(f"Expected exactly one object for {query}")
        obj = result["results"][0]
        conflicts = [key for key, value in fields.items() if obj.get(key) != value]
        if conflicts:
            raise RuntimeError(
                f"Existing {collection}/{fields['name']} differs in {', '.join(conflicts)}; "
                "review it explicitly before rerunning"
            )
        return obj["pulp_href"]

    def sync(self, repository, remote):
        print(f"Syncing {repository}", flush=True)
        self.wait(self.request("POST", repository + "sync/", {"remote": remote}))


def main():
    pulp = Pulp()
    for plugin, upstream in (("npm", "https://registry.npmjs.org/"),
                             ("python", "https://pypi.org/")):
        remote = pulp.ensure(f"remotes/{plugin}/{plugin}", {
            "name": f"{plugin}-upstream", "url": upstream, "policy": "on_demand",
        })
        fields = {"name": plugin, "base_path": plugin, "remote": remote,
                  "repository": None, "repository_version": None}
        if plugin == "python":
            fields["allow_uploads"] = False
            fields["publication"] = None
        kind = "pypi" if plugin == "python" else plugin
        pulp.ensure(f"distributions/{plugin}/{kind}", fields)

    print(f"npm registry: {pulp.url}/pulp/content/npm/")
    print(f"Python index: {pulp.url}/pypi/python/simple/")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        sys.exit(str(error))
