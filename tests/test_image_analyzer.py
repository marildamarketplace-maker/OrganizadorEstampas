from pathlib import Path
import tempfile
import unittest

from meury_app.image_analyzer import (
    ImageAnalysis,
    analyze_image,
    detect_device,
    parse_analysis_response,
    validate_image_path,
)


class FakeCuda:
    def __init__(self, available):
        self._available = available

    def is_available(self):
        return self._available


class FakeTorch:
    float16 = "float16"
    float32 = "float32"

    def __init__(self, cuda_available):
        self.cuda = FakeCuda(cuda_available)


class FakeAnalyzer:
    def __init__(self):
        self.received = None

    def analyze(self, image_path):
        self.received = image_path
        return ImageAnalysis("Arte floral", ["floral"], ["rosa"], ["flor"], ["natureza"], "floral")


class ImageAnalyzerTest(unittest.TestCase):
    def test_detects_cuda_and_cpu(self):
        self.assertEqual(detect_device(FakeTorch(True)), ("cuda", "float16"))
        self.assertEqual(detect_device(FakeTorch(False)), ("cpu", "float32"))

    def test_parses_and_normalizes_structured_response(self):
        result = parse_analysis_response("""```json
        {
          "description": "Estampa floral com rosas vermelhas.",
          "keywords": ["Floral", "Rosas", "floral"],
          "colors": "Vermelho, Verde, Branco",
          "elements": ["Rosas", "Folhas"],
          "themes": ["Natureza", "Romântico"],
          "category": "Floral"
        }
        ```""")

        self.assertEqual(result.keywords, ["floral", "rosas"])
        self.assertEqual(result.colors, ["vermelho", "verde", "branco"])
        self.assertEqual(result.category, "floral")
        self.assertTrue(result.processed)

    def test_rejects_response_without_keywords(self):
        with self.assertRaisesRegex(ValueError, "palavras-chave"):
            parse_analysis_response('{"description":"Arte abstrata","keywords":[]}')

    def test_independent_function_accepts_injected_analyzer(self):
        fake = FakeAnalyzer()
        result = analyze_image("imagem-inexistente.jpg", analyzer=fake)
        self.assertEqual(result.category, "floral")
        self.assertEqual(fake.received, "imagem-inexistente.jpg")

    def test_validates_supported_image_extensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "arte.pdf"
            pdf.write_bytes(b"pdf")
            with self.assertRaisesRegex(ValueError, "JPG, JPEG e PNG"):
                validate_image_path(pdf)


if __name__ == "__main__":
    unittest.main()
