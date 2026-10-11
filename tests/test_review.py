"""Blinded promotion-review packets and agreement measurement."""

import json
from pathlib import Path
import tempfile

from helpers import (
    EvaluatorTestCase,
)


class ReviewTests(EvaluatorTestCase):
    def test_prepare_review_creates_a_blinded_packet_bound_to_a_promotion_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, run_dir, _ = self.run_live_rubric(root, trials=3, promotion=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = root / "review-packet"

            prepared = self.run_cli(
                "prepare-review",
                "--run-dir",
                str(run_dir),
                "--output",
                str(packet),
            )

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
            template = json.loads((packet / "labels-template.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(manifest["required_reviewers"], 2)
            self.assertEqual(len(manifest["items"]), 3)
            self.assertEqual(template["manifest_sha256"], self.hash_file(packet / "manifest.json"))
            for item in manifest["items"]:
                prompt = (packet / item["prompt"]).read_text(encoding="utf-8")
                self.assertNotIn("control", prompt.casefold())
                self.assertNotIn("treatment", prompt.casefold())
                self.assertEqual(item["prompt_sha256"], self.hash_file(packet / item["prompt"]))


    def test_finalize_review_measures_human_and_automated_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, run_dir, _ = self.run_live_rubric(root, trials=3, promotion=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = root / "review-packet"
            prepared = self.run_cli(
                "prepare-review", "--run-dir", str(run_dir), "--output", str(packet)
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            template = json.loads((packet / "labels-template.json").read_text(encoding="utf-8"))
            attestation = json.loads(
                (packet / "holdout-attestation-template.json").read_text(encoding="utf-8")
            )
            attestation.update(
                {
                    "custodian_id": "client-custodian",
                    "independent_of_skill_authoring": True,
                    "unseen_during_development": True,
                    "coverage": {name: True for name in attestation["coverage"]},
                    "rationale": "The client controlled and categorized the held-out cases.",
                }
            )
            attestation_path = root / "holdout-attestation.json"
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            label_paths: list[Path] = []
            for reviewer_id in ("reviewer-a", "reviewer-b"):
                labels = json.loads(json.dumps(template))
                labels["reviewer_id"] = reviewer_id
                for item in labels["labels"]:
                    item["winner"] = "A"
                    item["rationale"] = f"{reviewer_id} prefers candidate A."
                    item["transcript_reviewed"] = True
                    for dimension in item["dimensions"]:
                        dimension["winner"] = "A"
                        dimension["rationale"] = f"A is stronger on {dimension['name']}."
                path = root / f"{reviewer_id}.json"
                path.write_text(json.dumps(labels), encoding="utf-8")
                label_paths.append(path)
            output = root / "promotion-review"

            finalized = self.run_cli(
                "finalize-review",
                "--run-dir",
                str(run_dir),
                "--manifest",
                str(packet / "manifest.json"),
                "--holdout-attestation",
                str(attestation_path),
                "--labels",
                str(label_paths[0]),
                "--labels",
                str(label_paths[1]),
                "--cost-usd",
                "1.25",
                "--cost-note",
                "Recorded test cost.",
                "--output",
                str(output),
            )

            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            review = json.loads((output / "promotion-review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["evidence_status"], "complete_human_review")
            self.assertEqual(review["reviewers"], ["reviewer-a", "reviewer-b"])
            self.assertEqual(review["human_agreement"]["overall"], {"agreements": 3, "total": 3})
            self.assertEqual(
                review["automated_judge_agreement"]["with_human_consensus"],
                {"agreements": 3, "total": 3},
            )
            self.assertEqual(
                review["automated_judge_agreement"]["by_reviewer"]["reviewer-a"]["overall"],
                {"agreements": 3, "total": 3},
            )
            self.assertEqual(review["transcript_review"]["reviewed_labels"], 6)
            self.assertEqual(review["cost"], {"usd": 1.25, "note": "Recorded test cost."})
            self.assertEqual(sum(review["outcomes"].values()), 3)
            self.assertEqual(review["trial_variance"]["choice"]["status"], "stable")
            markdown = (output / "promotion-review.md").read_text(encoding="utf-8")
            for artifact in review["artifacts"].values():
                retained = output / artifact["path"]
                self.assertTrue(retained.is_file())
                self.assertEqual(artifact["sha256"], self.hash_file(retained))
                self.assertIn(f"]({artifact['path']})", markdown)


    def test_finalize_review_rejects_incomplete_or_non_independent_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, run_dir, _ = self.run_live_rubric(root, trials=3, promotion=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = root / "review-packet"
            prepared = self.run_cli(
                "prepare-review", "--run-dir", str(run_dir), "--output", str(packet)
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            template = json.loads((packet / "labels-template.json").read_text(encoding="utf-8"))
            attestation = json.loads(
                (packet / "holdout-attestation-template.json").read_text(encoding="utf-8")
            )
            attestation.update(
                {
                    "custodian_id": "client-custodian",
                    "independent_of_skill_authoring": True,
                    "unseen_during_development": True,
                    "coverage": {name: True for name in attestation["coverage"]},
                    "rationale": "The client controlled and categorized the held-out cases.",
                }
            )
            attestation_path = root / "holdout-attestation.json"
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            label_paths = [root / "labels-a.json", root / "labels-b.json"]
            documents: list[dict[str, object]] = []
            for path in label_paths:
                labels = json.loads(json.dumps(template))
                labels["reviewer_id"] = "same-reviewer"
                for item in labels["labels"]:
                    item["winner"] = "A"
                    item["rationale"] = "Candidate A is stronger."
                    item["transcript_reviewed"] = True
                    for dimension in item["dimensions"]:
                        dimension["winner"] = "A"
                        dimension["rationale"] = "Candidate A is stronger."
                documents.append(labels)
                path.write_text(json.dumps(labels), encoding="utf-8")
            documents[0]["labels"][0]["transcript_reviewed"] = False
            label_paths[0].write_text(json.dumps(documents[0]), encoding="utf-8")

            arguments = (
                "finalize-review",
                "--run-dir",
                str(run_dir),
                "--manifest",
                str(packet / "manifest.json"),
                "--holdout-attestation",
                str(attestation_path),
                "--labels",
                str(label_paths[0]),
                "--labels",
                str(label_paths[1]),
                "--cost-usd",
                "0",
                "--cost-note",
                "Included in the test harness.",
                "--output",
                str(root / "review"),
            )
            incomplete = self.run_cli(*arguments)
            self.assertEqual(incomplete.returncode, 1)
            self.assertIn("transcript_reviewed: must be true", incomplete.stderr)

            documents[0]["labels"][0]["transcript_reviewed"] = True
            label_paths[0].write_text(json.dumps(documents[0]), encoding="utf-8")
            duplicated = self.run_cli(*arguments)
            self.assertEqual(duplicated.returncode, 1)
            self.assertIn("reviewer_id values must be distinct", duplicated.stderr)

            documents[1]["reviewer_id"] = "other-reviewer"
            label_paths[1].write_text(json.dumps(documents[1]), encoding="utf-8")
            attestation["coverage"]["adversarial"] = False
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            uncovered = self.run_cli(*arguments)
            self.assertEqual(uncovered.returncode, 1)
            self.assertIn("every required category must be true", uncovered.stderr)

            attestation["coverage"]["adversarial"] = True
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
            prompt_path = packet / manifest["items"][0]["prompt"]
            prompt_path.write_text("tampered", encoding="utf-8")
            tampered = self.run_cli(*arguments)
            self.assertEqual(tampered.returncode, 1)
            self.assertIn("prompt hash does not match the manifest", tampered.stderr)
