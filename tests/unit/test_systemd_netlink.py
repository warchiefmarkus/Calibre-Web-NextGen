from pathlib import Path

def test_calibre_web_systemd_allows_netlink_for_advocate_interface_detection():
    service = Path('deploy/systemd/calibre-web-nextgen.service').read_text(encoding='utf-8')
    line = next(line for line in service.splitlines() if line.startswith('RestrictAddressFamilies='))
    families = set(line.split('=', 1)[1].split())
    assert {'AF_UNIX', 'AF_INET', 'AF_INET6', 'AF_NETLINK'} <= families
