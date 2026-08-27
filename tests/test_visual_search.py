from pathlib import Path
import math
import pickle
import tempfile
import unittest
from unittest.mock import patch

import meury_app.visual_search as visual_module
from meury_app.visual_search import VisualSearchIndex, visual_record_identity


class Vectors(list):
    def astype(self, _dtype):
        return self


class FakeNp:
    @staticmethod
    def asarray(values, dtype=None):
        return Vectors(values)


class Flat:
    def __init__(self, dimension):
        self.d = dimension


class Index:
    def __init__(self, flat):
        self.d = flat.d
        self.vectors = {}

    @property
    def ntotal(self):
        return len(self.vectors)

    def add_with_ids(self, vectors, ids):
        for vector, vector_id in zip(vectors, ids):
            self.vectors[int(vector_id)] = list(vector)

    def remove_ids(self, ids):
        for vector_id in ids:
            self.vectors.pop(int(vector_id), None)

    def search(self, queries, limit):
        scored = []
        query = queries[0]
        for vector_id, vector in self.vectors.items():
            scored.append((sum(a * b for a, b in zip(query, vector)), vector_id))
        scored.sort(reverse=True)
        selected = scored[:limit]
        return [[score for score, _id in selected]], [[vector_id for _score, vector_id in selected]]


class Faiss:
    IndexFlatIP = Flat
    IndexIDMap2 = Index

    @staticmethod
    def write_index(index, path):
        with open(path, "wb") as stream:
            pickle.dump(index, stream)

    @staticmethod
    def read_index(path):
        with open(path, "rb") as stream:
            return pickle.load(stream)


class EncoderIndex(VisualSearchIndex):
    encoded = []

    def _encode_paths(self, paths):
        self.encoded.extend(paths)
        vectors = []
        for path in paths:
            position = sum(path.encode()) % 768
            vector = [0.0] * 768
            vector[position] = 1.0
            vectors.append(vector)
        return Vectors(vectors)


def image(relative, size=100, mtime=10):
    return {
        "active": True, "source": 0, "relative_path": relative,
        "filename": Path(relative).name, "path": f"/artes/{relative}",
        "size": size, "mtime_ns": mtime,
    }


class VisualSearchTest(unittest.TestCase):
    def test_incremental_index_reuses_move_and_recalculates_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [image("100/rosa.jpg"), image("200/natal.png")]
            dependencies = (Faiss, FakeNp, object(), object(), object(), object(), object())
            EncoderIndex.encoded = []
            with (
                patch.object(visual_module, "VISUAL_INDEX_FILE", root / "visual.faiss"),
                patch.object(visual_module, "VISUAL_METADATA_FILE", root / "visual.jsonl"),
                patch.object(visual_module, "VISUAL_LOG_FILE", root / "visual.log"),
                patch.object(visual_module, "ensure_app_dir"),
                patch.object(visual_module, "_dependencies", return_value=dependencies),
                patch.object(
                    visual_module, "load_catalog_records",
                    side_effect=lambda _sources: records,
                ),
            ):
                first = EncoderIndex([root]).update(rebuild=True)
                self.assertEqual(first.added, 2)
                encoded_after_first = len(EncoderIndex.encoded)

                records[0] = {**records[0], "relative_path": "NOVO/rosa-renomeada.jpg",
                              "filename": "rosa-renomeada.jpg",
                              "path": "/artes/NOVO/rosa-renomeada.jpg"}
                records[1]["mtime_ns"] = 20
                second_engine = EncoderIndex([root])
                second = second_engine.update()

                mapping = {entry["identity"] for entry in second_engine.entries}

            self.assertEqual(second.reused, 1)
            self.assertEqual(second.updated, 1)
            self.assertEqual(len(EncoderIndex.encoded) - encoded_after_first, 1)
            self.assertIn(visual_record_identity(records[0]), mapping)

    def test_similarity_search_excludes_selected_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [image("100/rosa.jpg"), image("200/natal.png")]
            dependencies = (Faiss, FakeNp, object(), object(), object(), object(), object())
            with (
                patch.object(visual_module, "VISUAL_INDEX_FILE", root / "visual.faiss"),
                patch.object(visual_module, "VISUAL_METADATA_FILE", root / "visual.jsonl"),
                patch.object(visual_module, "VISUAL_LOG_FILE", root / "visual.log"),
                patch.object(visual_module, "ensure_app_dir"),
                patch.object(visual_module, "_dependencies", return_value=dependencies),
                patch.object(visual_module, "load_catalog_records", return_value=records),
            ):
                engine = EncoderIndex([root])
                engine.update(rebuild=True)
                by_identity = {visual_record_identity(record): record for record in records}
                results = engine.search_similar(
                    records[0]["path"], by_identity,
                    exclude_identity=visual_record_identity(records[0]),
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], records[1])
            self.assertGreaterEqual(results[0][1], 0.0)
            self.assertLessEqual(results[0][1], 1.0)


if __name__ == "__main__":
    unittest.main()
