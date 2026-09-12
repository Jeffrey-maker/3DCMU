import unittest

from cmu_route_examples import build_scott_wean_router


class CmuRouteExamplesTest(unittest.TestCase):
    def setUp(self):
        self.router = build_scott_wean_router()

    def test_cross_building_route_uses_level_four_passage(self):
        route = self.router.route("SH-4N103", "WEH-4325")
        instructions = " ".join(step["instruction"] for step in route["steps"])
        self.assertIn("passage into Wean Hall", instructions)
        self.assertEqual(route["destination"], "Wean 4325")

    def test_cross_floor_route_uses_stair_when_shorter(self):
        route = self.router.route("WEH-5130", "WEH-4325")
        instructions = " ".join(step["instruction"] for step in route["steps"])
        self.assertIn("Descend one floor", instructions)

    def test_wheelchair_route_uses_elevator(self):
        route = self.router.route("WEH-5130", "WEH-4325", wheelchair=True)
        instructions = " ".join(step["instruction"] for step in route["steps"])
        self.assertIn("elevator down", instructions)
        self.assertNotIn("Descend one floor", instructions)


if __name__ == "__main__":
    unittest.main()
