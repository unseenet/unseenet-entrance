"""unseenet.releases also carries the nightly portable and bootable builds: the
entrance keeps only the web client's posts, found by their Subject in the headers."""
from unseenet_entrance.listfetch import WEB_RELEASE_SUBJECT, ListCache, _subject
from unseenet_entrance import release, server


def test_subject_from_headers():
    head = b"Path: x\r\nSubject: Web client 20261025.1251\r\nFrom: y\r\n"
    assert _subject(head).startswith(WEB_RELEASE_SUBJECT)
    assert _subject(b"subject: Unseenet 0.5.1.dev104, dev\r\n") == "Unseenet 0.5.1.dev104, dev"
    assert _subject(b"From: y\r\n") == ""


def test_the_releases_cache_keeps_web_client_posts_only():
    assert ListCache("g").subject is None                    # the list of entrances: all
    assert ListCache("g", WEB_RELEASE_SUBJECT).subject == "Web client "
    import inspect
    assert "WEB_RELEASE_SUBJECT" in inspect.getsource(release.install_newest)
    assert "WEB_RELEASE_SUBJECT" in inspect.getsource(server)
