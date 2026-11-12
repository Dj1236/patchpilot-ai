import pytest

from patchpilot.intake.urls import IssueRef, IssueUrlError, parse_issue_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/octocat/Hello-World/issues/1", ("octocat", "hello-world", 1)),
        ("https://github.com/octocat/Hello-World/issues/1/", ("octocat", "hello-world", 1)),
        ("https://www.github.com/octocat/hello-world/issues/42", ("octocat", "hello-world", 42)),
        ("HTTPS://GitHub.com/OctoCat/Hello-World/issues/7", ("octocat", "hello-world", 7)),
        (
            "https://github.com/octocat/Hello-World/issues/9#issuecomment-123",
            ("octocat", "hello-world", 9),
        ),
        (
            "https://github.com/octocat/Hello-World/issues/9?utm_source=x",
            ("octocat", "hello-world", 9),
        ),
        ("https://github.com/a/b/issues/1", ("a", "b", 1)),
        (
            "https://github.com/my-org/my.repo_name-2/issues/1234567890",
            ("my-org", "my.repo_name-2", 1234567890),
        ),
    ],
)
def test_valid_issue_urls_are_normalized(url: str, expected: tuple[str, str, int]) -> None:
    ref = parse_issue_url(url)

    assert (ref.owner, ref.repository, ref.number) == expected


def test_canonical_form_drops_everything_but_the_identity() -> None:
    ref = parse_issue_url("https://WWW.github.com/OctoCat/Hello-World/issues/5/?a=b#frag")

    assert ref.slug == "octocat/hello-world"
    assert ref.canonical_url == "https://github.com/octocat/hello-world/issues/5"


@pytest.mark.parametrize(
    "url",
    [
        # wrong scheme or host
        "http://github.com/octocat/Hello-World/issues/1",
        "git://github.com/octocat/Hello-World/issues/1",
        "ssh://git@github.com/octocat/Hello-World/issues/1",
        "file:///etc/passwd",
        "github.com/octocat/Hello-World/issues/1",
        "//github.com/octocat/Hello-World/issues/1",
        "https://gitlab.com/octocat/Hello-World/issues/1",
        "https://github.com.evil.example/octocat/Hello-World/issues/1",
        "https://evilgithub.com/octocat/Hello-World/issues/1",
        "https://api.github.com/repos/octocat/Hello-World/issues/1",
        "https://gist.github.com/octocat/Hello-World/issues/1",
        "https://raw.githubusercontent.com/octocat/Hello-World/issues/1",
        # credentials, ports and host confusion
        "https://github.com@evil.example/octocat/Hello-World/issues/1",
        "https://user:pass@github.com/octocat/Hello-World/issues/1",
        "https://github.com:443/octocat/Hello-World/issues/1",
        "https://github.com:8080/octocat/Hello-World/issues/1",
        "https://github.com\\@evil.example/octocat/Hello-World/issues/1",
        "https://github.com#@evil.example/octocat/Hello-World/issues/1",
        "https://evil.example/https://github.com/octocat/Hello-World/issues/1",
        "https://github.com./octocat/Hello-World/issues/1",
        "https://[::1]/octocat/Hello-World/issues/1",
        "https://127.0.0.1/octocat/Hello-World/issues/1",
        # not an issue
        "https://github.com/octocat/Hello-World/pull/1",
        "https://github.com/octocat/Hello-World/pulls/1",
        "https://github.com/octocat/Hello-World/issues",
        "https://github.com/octocat/Hello-World/issues/",
        "https://github.com/octocat/Hello-World/issues/new",
        "https://github.com/octocat/Hello-World",
        "https://github.com/octocat/Hello-World/issues/1/comments",
        "https://github.com/octocat/Hello-World/tree/main/issues/1",
        # bad numbers
        "https://github.com/octocat/Hello-World/issues/0",
        "https://github.com/octocat/Hello-World/issues/-1",
        "https://github.com/octocat/Hello-World/issues/01",
        "https://github.com/octocat/Hello-World/issues/1e3",
        "https://github.com/octocat/Hello-World/issues/12345678901",
        "https://github.com/octocat/Hello-World/issues/1.0",
        # traversal and encoding
        "https://github.com/octocat/../issues/1",
        "https://github.com/../octocat/Hello-World/issues/1",
        "https://github.com/octocat/Hello-World/../../evil/repo/issues/1",
        "https://github.com/octocat/Hello-World/issues/../pull/1",
        "https://github.com/octocat/Hello%2DWorld/issues/1",
        "https://github.com/octocat%2Fevil/Hello-World/issues/1",
        "https://github.com/octocat/Hello-World/issues/%31",
        "https://github.com//octocat/Hello-World/issues/1",
        "https://github.com/octocat//issues/1",
        # bad names
        "https://github.com/-octocat/Hello-World/issues/1",
        "https://github.com/octocat-/Hello-World/issues/1",
        "https://github.com/octo--cat/Hello-World/issues/1",
        "https://github.com/octo_cat/Hello-World/issues/1",
        "https://github.com/" + "a" * 40 + "/Hello-World/issues/1",
        "https://github.com/octocat/" + "a" * 101 + "/issues/1",
        "https://github.com/octocat/./issues/1",
        "https://github.com/octocat/Hello-World.git/issues/1",
        "https://github.com/octocat/Hello World/issues/1",
        # shell and injection material
        "https://github.com/octocat/Hello-World/issues/1; rm -rf /",
        "https://github.com/octocat/$(id)/issues/1",
        "https://github.com/octocat/`id`/issues/1",
        "https://github.com/octocat/Hello-World/issues/1\nHost: evil.example",
        "https://github.com/octocat/Hello-World/issues/1\x00",
        "https://github.com/octocat/Hello-World/issues/1 ",
        " https://github.com/octocat/Hello-World/issues/1",
        "https://github.com/octocat/Hello-World/issues/1?a=<script>",
        "https://github.com/octocat/Hello-World/issues/1#a b",
        # look-alike characters: full-width digit, dotless i, Cyrillic letters
        "https://github.com/octocat/Hello-World/issues/\uff11",
        "https://g\u0131thub.com/octocat/Hello-World/issues/1",
        "https://github.com/\u043e\u0441tocat/Hello-World/issues/1",
        "",
        "https://github.com/octocat/Hello-World/issues/1" + "?" + "a" * 400,
    ],
)
def test_hostile_or_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(IssueUrlError):
        parse_issue_url(url)


@pytest.mark.parametrize("value", [None, 12, b"https://github.com/a/b/issues/1", ["x"]])
def test_non_text_input_is_rejected(value: object) -> None:
    with pytest.raises(IssueUrlError):
        parse_issue_url(value)  # type: ignore[arg-type]


def test_issue_ref_validates_its_own_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IssueRef(owner="../evil", repository="repo", number=1)
    with pytest.raises(ValidationError):
        IssueRef(owner="octocat", repository="re po", number=1)
    with pytest.raises(ValidationError):
        IssueRef(owner="octocat", repository="repo", number=0)
