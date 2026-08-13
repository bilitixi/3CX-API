from app.core.config import Settings


def test_sequence_dns_list_parses_comma_separated_value():
    settings = Settings(threecx_sequence_dns="1003,1005,1006")

    assert settings.sequence_dns_list == ["1003", "1005", "1006"]


def test_sequence_dns_list_strips_whitespace_and_drops_empties():
    settings = Settings(threecx_sequence_dns=" 1003 ,1005,, 1006 ")

    assert settings.sequence_dns_list == ["1003", "1005", "1006"]


def test_sequence_dns_list_empty_when_unset():
    settings = Settings(threecx_sequence_dns="")

    assert settings.sequence_dns_list == []
