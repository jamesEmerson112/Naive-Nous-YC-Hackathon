"""`/technician` with no case yet renders a page in the shell, not a bare line (redesign)."""


def test_no_case_yet_is_a_styled_empty_state(client):
    page = client.get("/technician", follow_redirects=False)
    assert page.status_code == 200
    assert 'class="empty"' in page.text and "No Naïve technician cases yet" in page.text
    assert '<a class="nav" href="/technician">Technician</a>' in page.text  # the shared shell
    assert "TECHNICIAN_MODE=off" in page.text  # the test settings pin technician mode off
