from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import meury_app.semantic_search as semantic_module
from meury_app.art_search import SearchResult
from meury_app.semantic_search import (
    SemanticSearchIndex, merge_hybrid_results, record_identity,
    semantic_content_hash, semantic_document,
)


class FakeVectors(list):
    def astype(self, _dtype):
        return self


class FakeModel:
    encoded = []

    def __init__(self, *_args, **_kwargs):
        pass

    def encode(self, texts, **_kwargs):
        self.encoded.extend(texts)
        return FakeVectors([[float(len(text) % 7)] * 384 for text in texts])


class FakeCuda:
    @staticmethod
    def is_available():
        return False


class FakeTorch:
    cuda = FakeCuda()


class FakeNp:
    @staticmethod
    def asarray(values, dtype=None):
        return list(values)


class FakeFlat:
    def __init__(self, dimension):
        self.d = dimension


class FakeIndex:
    def __init__(self, flat):
        self.d = flat.d
        self.vectors = {}

    @property
    def ntotal(self):
        return len(self.vectors)

    def add_with_ids(self, vectors, ids):
        for vector, vector_id in zip(vectors, ids):
            self.vectors[int(vector_id)] = vector

    def remove_ids(self, ids):
        for vector_id in ids:
            self.vectors.pop(int(vector_id), None)


class FakeFaiss:
    IndexFlatIP = FakeFlat
    IndexIDMap2 = FakeIndex

    @staticmethod
    def write_index(index, path):
        with open(path, "wb") as stream:
            pickle.dump(index, stream)

    @staticmethod
    def read_index(path):
        with open(path, "rb") as stream:
            return pickle.load(stream)


def art(relative, description, keywords):
    return {
        "active": True, "source": 0, "relative_path": relative,
        "filename": Path(relative).name, "description": description,
        "keywords": keywords, "colors": [], "elements": [], "themes": [],
        "category": "floral",
    }


class SemanticSearchTest(unittest.TestCase):
    def test_document_and_hash_change_only_with_semantic_content(self):
        record = art("100/100.jpg", "Rosas vermelhas", ["floral"])
        first = semantic_content_hash(record)
        record["path"] = "D:/OUTRO/100.jpg"
        self.assertEqual(first, semantic_content_hash(record))
        record["keywords"].append("romântico")
        self.assertNotEqual(first, semantic_content_hash(record))
        self.assertIn("descrição: Rosas vermelhas", semantic_document(record))

    def test_incremental_update_encodes_only_new_and_changed_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path = root / "semantic.faiss"
            metadata_path = root / "semantic.jsonl"
            log_path = root / "semantic.log"
            records = [
                art("100/100.jpg", "Rosas vermelhas", ["floral"]),
                art("200/200.jpg", "Papai Noel", ["natal"]),
            ]
            encoded = []
            dependencies = (FakeFaiss, FakeNp)

            def fake_encode(_engine, texts, prefix, _batch_size):
                encoded.extend(prefix + text for text in texts)
                return FakeVectors([[float(len(text) % 7)] * 384 for text in texts])

            with (
                patch.object(semantic_module, "SEMANTIC_INDEX_FILE", index_path),
                patch.object(semantic_module, "SEMANTIC_METADATA_FILE", metadata_path),
                patch.object(semantic_module, "SEMANTIC_LOG_FILE", log_path),
                patch.object(semantic_module, "ensure_app_dir"),
                patch.object(semantic_module, "_load_dependencies", return_value=dependencies),
                patch.object(SemanticSearchIndex, "_encode", fake_encode),
                patch.object(
                    semantic_module, "load_catalog_records",
                    side_effect=lambda _sources: records,
                ),
            ):
                engine = SemanticSearchIndex([root])
                first = engine.update(rebuild=True, batch_size=10)
                self.assertEqual(first.added, 2)
                first_encoded = len(encoded)

                records[0]["keywords"].append("delicada")
                records.pop(1)
                records.append(art("300/300.jpg", "Girassóis", ["floral"]))
                second = SemanticSearchIndex([root]).update(batch_size=10)

            self.assertEqual(second.added, 1)
            self.assertEqual(second.updated, 1)
            self.assertEqual(second.removed, 1)
            self.assertEqual(len(encoded) - first_encoded, 2)

    def test_hybrid_ranking_keeps_textual_and_semantic_candidates(self):
        textual = art("1/1.jpg", "flor", ["flor"])
        conceptual = art("2/2.jpg", "borboleta delicada", ["pastel"])
        results = merge_hybrid_results(
            [SearchResult(textual, 10.0)], [(conceptual, 0.9)], limit=10
        )
        identities = [record_identity(record) for record, _score in results]
        self.assertIn(record_identity(textual), identities)
        self.assertIn(record_identity(conceptual), identities)
        self.assertEqual(results[0][0], conceptual)


if __name__ == "__main__":
    unittest.main()
