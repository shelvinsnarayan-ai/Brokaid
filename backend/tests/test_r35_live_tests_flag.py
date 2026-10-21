"""R3.4c — tests that create client messages over HTTP must flag them as test at creation (or use the in-process test DB)."""
import pathlib
import re

TESTS = pathlib.Path(__file__).resolve().parent
POSTS = re.compile(r"\.post\(f?\"(\{BASE\})?/api/c/(ask/message|claims/[^\"]*/thread)\"")


def test_http_message_posts_are_flagged_or_in_process():
    offenders = []
    for f in TESTS.glob("test_*.py"):
        src = f.read_text()
        if not POSTS.search(src) or "app_client" in src:  # in-process app_client tests use the throwaway per-run DB
            continue
        if "_flag_test_thread(" not in src:
            offenders.append(f.name)
    assert offenders == [], f"live tests posting client messages without flagging them as test: {offenders}"


def test_no_hardcoded_preview_base_in_tests():
    hard = [f.name for f in TESTS.glob("test_*.py")
            if re.search(r"BASE\s*=.*\"https://[a-z0-9-]+\.preview\.emergentagent\.com", f.read_text())]
    assert hard == [], hard
