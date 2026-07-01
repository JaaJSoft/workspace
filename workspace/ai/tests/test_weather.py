from unittest.mock import MagicMock, patch

from django.test import TestCase

from workspace.ai.services.weather import (
    describe_weather_code,
    geocode,
    get_current_weather,
)


def _mock_client(json_return):
    """Build a context-manager httpx.Client mock returning *json_return*."""
    resp = MagicMock()
    resp.json.return_value = json_return
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.return_value = resp
    return client


class DescribeWeatherCodeTests(TestCase):
    def test_known_code(self):
        self.assertEqual(describe_weather_code(0), "Clear sky")
        self.assertEqual(describe_weather_code(95), "Thunderstorm")

    def test_string_code(self):
        self.assertEqual(describe_weather_code("61"), "Slight rain")

    def test_unknown_code(self):
        self.assertEqual(describe_weather_code(1234), "Unknown conditions")

    def test_none_code(self):
        self.assertEqual(describe_weather_code(None), "Unknown conditions")


class GeocodeTests(TestCase):
    @patch("workspace.ai.services.weather.httpx.Client")
    def test_resolves_place(self, mock_client_cls):
        mock_client_cls.return_value = _mock_client(
            {
                "results": [
                    {
                        "name": "Paris",
                        "admin1": "Île-de-France",
                        "country": "France",
                        "latitude": 48.85,
                        "longitude": 2.35,
                    }
                ]
            }
        )

        place = geocode("Paris")

        self.assertEqual(place["latitude"], 48.85)
        self.assertEqual(place["longitude"], 2.35)
        self.assertEqual(place["country"], "France")
        self.assertIn("Paris", place["name"])
        self.assertIn("France", place["name"])

    def test_empty_name_returns_none(self):
        self.assertIsNone(geocode("   "))

    @patch("workspace.ai.services.weather.httpx.Client")
    def test_no_results_returns_none(self, mock_client_cls):
        mock_client_cls.return_value = _mock_client({"results": []})
        self.assertIsNone(geocode("Nowhereville"))

    @patch("workspace.ai.services.weather.httpx.Client")
    def test_http_error_returns_none(self, mock_client_cls):
        import httpx

        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.side_effect = httpx.ConnectError("boom")
        mock_client_cls.return_value = client

        self.assertIsNone(geocode("Paris"))


class GetCurrentWeatherTests(TestCase):
    @patch("workspace.ai.services.weather.httpx.Client")
    def test_returns_weather(self, mock_client_cls):
        geo = _mock_client(
            {
                "results": [
                    {
                        "name": "Tokyo",
                        "country": "Japan",
                        "latitude": 35.68,
                        "longitude": 139.69,
                    }
                ]
            }
        )
        forecast = _mock_client(
            {
                "current": {
                    "temperature_2m": 18.4,
                    "apparent_temperature": 17.1,
                    "relative_humidity_2m": 55,
                    "wind_speed_10m": 12.0,
                    "weather_code": 61,
                    "is_day": 1,
                },
                "current_units": {
                    "temperature_2m": "°C",
                    "relative_humidity_2m": "%",
                    "wind_speed_10m": "km/h",
                },
            }
        )
        # geocode() is the first Client(), forecast the second.
        mock_client_cls.side_effect = [geo, forecast]

        weather = get_current_weather("Tokyo")

        self.assertEqual(weather["location"], "Tokyo, Japan")
        self.assertEqual(weather["temperature"], 18.4)
        self.assertEqual(weather["feels_like"], 17.1)
        self.assertEqual(weather["humidity"], 55)
        self.assertEqual(weather["conditions"], "Slight rain")
        self.assertEqual(weather["weather_code"], 61)
        self.assertTrue(weather["is_day"])

    @patch("workspace.ai.services.weather.httpx.Client")
    def test_unknown_place_returns_none(self, mock_client_cls):
        mock_client_cls.return_value = _mock_client({"results": []})
        self.assertIsNone(get_current_weather("Nowhereville"))

    @patch("workspace.ai.services.weather.httpx.Client")
    def test_forecast_error_returns_none(self, mock_client_cls):
        import httpx

        geo = _mock_client(
            {
                "results": [
                    {
                        "name": "Paris",
                        "country": "France",
                        "latitude": 48.85,
                        "longitude": 2.35,
                    }
                ]
            }
        )
        forecast = MagicMock()
        forecast.__enter__ = MagicMock(return_value=forecast)
        forecast.__exit__ = MagicMock(return_value=False)
        forecast.get.side_effect = httpx.ConnectError("boom")
        mock_client_cls.side_effect = [geo, forecast]

        self.assertIsNone(get_current_weather("Paris"))
