from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

import meury_app.art_search as search_module
from meury_app.art_search import ArtSearchEngine, ThumbnailCache, normalize_search_text


class ArtSearchTest(unittest.TestCase):
    def records(self):
        return [
            {
                "active": True, "filename": "100-ROSAS.jpg",
                "path": "/artes/florais/100-ROSAS.jpg",
                "description": "Estampa floral com rosas vermelhas e folhas verdes",
                "keywords": ["rosa", "rosas", "floral", "flores"],
                "colors": ["vermelho", "verde"], "elements": ["folhas"],
                "themes": ["natureza", "romântico"], "category": "floral",
            },
            {
                "active": True, "filename": "200-NATAL.png",
                "path": "/artes/natal/200-NATAL.png", "description": "Papai Noel",
                "keywords": ["natal", "papai noel"], "colors": ["vermelho"],
                "elements": ["barba", "gorro"], "themes": ["natalino"],
                "category": "natal",
            },
            {"active": False, "filename": "removida.jpg", "keywords": ["flor"]},
        ]

    def test_searches_flexibly_across_metadata(self):
        engine = ArtSearchEngine([Path("/artes")])
        with patch.object(
            search_module, "load_catalog_records",
            return_value=self.records(),
        ):
            self.assertEqual(engine.load(), 2)

        floral = engine.search("flor vermelha")
        natal = engine.search("Papai Noel vermelho")

        self.assertEqual(floral[0].record["filename"], "100-ROSAS.jpg")
        self.assertEqual(natal[0].record["filename"], "200-NATAL.png")

    def test_normalizes_accents_and_ignores_connector_words(self):
        self.assertEqual(normalize_search_text("Nossa Senhora, AZUL"), "nossa senhora azul")
        engine = ArtSearchEngine([])
        engine._documents = []
        self.assertEqual(engine.search("e de com"), [])

    @unittest.skipUnless(Image is not None, "Pillow não está instalado neste ambiente")
    def test_thumbnail_cache_reuses_thumbnail_and_invalidates_changed_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "arte.png"
            cache = ThumbnailCache(root / "cache")
            Image.new("RGB", (1200, 800), "red").save(source)

            first = cache.get_or_create(source, (240, 180))
            second = cache.get_or_create(source, (240, 180))
            self.assertEqual(first, second)
            with Image.open(first) as thumbnail:
                self.assertEqual(thumbnail.size, (240, 180))

            time.sleep(0.001)
            Image.new("RGB", (1200, 800), "blue").save(source)
            changed = cache.get_or_create(source, (240, 180))
            self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
