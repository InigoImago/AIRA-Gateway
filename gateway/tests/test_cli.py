from aira_gateway import cli


def test_cli_create_prints_key(capsys) -> None:
    exit_code = cli.main(["api-key", "create", "--subject", "cli-user", "--label", "svc"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "aira_" in output
    assert "prefix=" in output


def test_cli_revoke_missing_prefix(capsys) -> None:
    exit_code = cli.main(["api-key", "revoke", "--prefix", "doesnotexist"])
    assert exit_code == 1
    assert "no active key" in capsys.readouterr().out
