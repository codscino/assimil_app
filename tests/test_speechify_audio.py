import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import speechify_audio


class SpeechifyAudioTests(unittest.TestCase):
    @patch("speechify_audio.Speechify")
    def test_synthesis_uses_french_simba_mp3_at_07x_speed(self, speechify_class):
        client = speechify_class.return_value
        client.audio.speech.return_value = SimpleNamespace(
            audio_data=base64.b64encode(b"mp3 bytes").decode("ascii")
        )

        audio = speechify_audio.synthesize_french_audio(
            "secret-key", "amelie", "Café & thé < chaud"
        )

        self.assertEqual(audio, b"mp3 bytes")
        speechify_class.assert_called_once_with(token="secret-key")
        client.audio.speech.assert_called_once_with(
            input=(
                '<speak><prosody rate="-30%">'
                "Café &amp; thé &lt; chaud</prosody></speak>"
            ),
            voice_id="amelie",
            model="simba-3.0",
            language="fr-FR",
            audio_format="mp3",
        )

    @patch("speechify_audio.Speechify")
    def test_voice_listing_filters_and_normalizes_french_simba_voices(
        self, speechify_class
    ):
        client = speechify_class.return_value
        client.voices.list.return_value = [
            SimpleNamespace(
                id="amelie",
                display_name="Amélie",
                gender="female",
                locale="fr-FR",
                type="shared",
            )
        ]

        voices = speechify_audio.list_french_voices("secret-key")

        self.assertEqual(
            voices,
            [
                {
                    "id": "amelie",
                    "display_name": "Amélie",
                    "gender": "female",
                    "locale": "fr-FR",
                    "type": "shared",
                }
            ],
        )
        client.voices.list.assert_called_once_with(
            locale="fr", model="simba-3.0", limit=200
        )


if __name__ == "__main__":
    unittest.main()
