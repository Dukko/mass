from mass.config import Config


def test_endpoint_name_does_not_expose_the_hostname():
    assert Config().endpoint_name == "Mass"
