"""A small, stateful stand-in for the GitHub REST API, served through httpx.

It keeps repositories, branches, git objects and pull requests in memory,
answers the endpoints ``GitHubWriter`` may use, records every request, and
can be told to fail or time out at a chosen step. No request leaves the
machine.
"""

import base64
import hashlib
import json
import re
from typing import Any

import httpx
from pydantic import SecretStr

from patchpilot.submit.submission import GitHubWriter, git_blob_sha

OWNER, NAME, REPOSITORY_ID = "acme", "cachekit", 4242
BASE_COMMIT = "c0ffee" + "0" * 34
BASE_TREE = "7ree" + "0" * 36
TOKEN = "github_pat_patchpilot_canary_not_real"


def _sha(material: Any) -> str:
    text = json.dumps(material, sort_keys=True)
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()


class FakeGitHub:
    def __init__(self) -> None:
        self.repo = {
            "id": REPOSITORY_ID,
            "full_name": f"{OWNER}/{NAME}",
            "default_branch": "main",
            "permissions": {"push": True},
        }
        self.branches = {"main": BASE_COMMIT}
        self.commits: dict[str, dict[str, Any]] = {BASE_COMMIT: {"tree": BASE_TREE, "parents": []}}
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, Any] = {}
        self.pulls: list[dict[str, Any]] = []
        self.requests: list[str] = []
        self.bodies: list[str] = []
        # "METHOD path-suffix" pattern -> "timeout-before", "timeout-after" or an HTTP status.
        self.failures: dict[str, Any] = {}
        self.classic_token = False
        self.counter = 0

    @property
    def writes(self) -> list[str]:
        return [request for request in self.requests if not request.startswith("GET")]

    def writer(self) -> GitHubWriter:
        return GitHubWriter(
            f"{OWNER}/{NAME}", SecretStr(TOKEN), transport=httpx.MockTransport(self)
        )

    def _respond(self, status: int, body: Any = None) -> httpx.Response:
        headers = {"x-oauth-scopes": "repo"} if self.classic_token else {}
        return httpx.Response(status, json=body, headers=headers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        key = f"{request.method} {path.removeprefix(f'/repos/{OWNER}/{NAME}')}"
        self.requests.append(f"{request.method} {path}")
        self.bodies.append(request.content.decode())
        failure = next(
            (value for pattern, value in self.failures.items() if re.fullmatch(pattern, key)),
            None,
        )
        if failure == "timeout-before":
            raise httpx.ReadTimeout("timed out", request=request)
        if isinstance(failure, int):
            return self._respond(failure, {"message": "injected"})
        response = self._handle(request, key)
        if failure == "timeout-after":
            raise httpx.ReadTimeout("timed out after the write arrived", request=request)
        return response

    def _handle(self, request: httpx.Request, key: str) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        method, _, rest = key.partition(" ")
        if key == "GET ":
            return self._respond(200, self.repo)
        if match := re.fullmatch(r"/branches/(.+)", rest):
            sha = self.branches.get(match[1])
            return self._respond(200, {"commit": {"sha": sha}}) if sha else self._respond(404)
        if match := re.fullmatch(r"/git/commits/([0-9a-f]{40})", rest):
            commit = self.commits.get(match[1])
            if commit is None:
                return self._respond(404)
            parents = [{"sha": parent} for parent in commit["parents"]]
            data = {"sha": match[1], "tree": {"sha": commit["tree"]}, "parents": parents}
            return self._respond(200, data)
        if method == "GET" and (match := re.fullmatch(r"/git/ref/heads/(.+)", rest)):
            sha = self.branches.get(match[1])
            return self._respond(200, {"object": {"sha": sha}}) if sha else self._respond(404)
        if key == "GET /pulls":
            head = request.url.params.get("head", "").split(":", 1)[-1]
            return self._respond(200, [p for p in self.pulls if p["head"]["ref"] == head])
        if key == "POST /git/blobs":
            blob = base64.b64decode(body["content"])
            self.blobs[git_blob_sha(blob)] = blob
            return self._respond(201, {"sha": git_blob_sha(blob)})
        if key == "POST /git/trees":
            sha = _sha(body)
            self.trees[sha] = body
            return self._respond(201, {"sha": sha})
        if key == "POST /git/commits":
            self.counter += 1
            sha = _sha({**body, "n": self.counter})
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"]}
            return self._respond(201, {"sha": sha})
        if key == "POST /git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.branches:
                return self._respond(422, {"message": "Reference already exists"})
            self.branches[name] = body["sha"]
            return self._respond(201, {"ref": body["ref"], "object": {"sha": body["sha"]}})
        if key == "POST /pulls":
            number = len(self.pulls) + 1
            pull = {
                "number": number,
                "html_url": f"https://github.com/{OWNER}/{NAME}/pull/{number}",
                "draft": body["draft"],
                "title": body["title"],
                "body": body["body"],
                "head": {"ref": body["head"], "sha": self.branches[body["head"]]},
                "base": {"ref": body["base"]},
            }
            self.pulls.append(pull)
            return self._respond(201, pull)
        return self._respond(404, {"message": "Not Found"})

    def files_on(self, branch: str) -> dict[str, bytes]:
        """The files the branch's commit adds or changes, read back from the fake's objects."""
        tree = self.trees[self.commits[self.branches[branch]]["tree"]]
        return {entry["path"]: self.blobs[entry["sha"]] for entry in tree["tree"]}
