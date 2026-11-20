from pathlib import Path
import importlib.util
import tempfile
import unittest

module = Path(__file__).resolve().parents[1] / "adversarial/matches.py"
spec = importlib.util.spec_from_file_location("adversarial_matches", module)
matches = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matches)


class PanelVerification(unittest.TestCase):
    def test_validate_color_and_perspective(self):
        cell = dict(arm="baseline", opponent="fruit")
        root = dict(id="x", fen=matches.START, family="root", group="quiet")
        def game(white, black, result):
            return (f'[Round "1"]\n[White "{white}"]\n[Black "{black}"]\n'
                    f'[Result "{result}"]\n[Termination "normal"]\n\n{result}\n\n')
        first = game("arm_baseline", "vs_fruit", "1-0")
        second = game("vs_fruit", "arm_baseline", "1-0")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"games.pgn"
            p.write_text(second+first)
            rows = matches.parse_games(p, cell, [root])
            self.assertEqual([r["engine_score"] for r in rows], [0,1])
            p.write_text(first+first)
            with self.assertRaises(ValueError):
                matches.parse_games(p, cell, [root])


if __name__ == "__main__":
    unittest.main()
