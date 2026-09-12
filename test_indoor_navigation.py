import unittest

from indoor_navigation import IndoorMap3D


class IndoorNavigationTest(unittest.TestCase):
    def test_route_crosses_buildings(self):
        nav = IndoorMap3D()
        nav.add_floor("A", 0, [".....", ".###.", "....."], rooms={"lobby": (0, 0), "office": (2, 4)})
        nav.add_floor("B", 0, [".....", ".###.", "....."], rooms={"lab": (0, 4)})
        nav.connect("skybridge", ("A", 0, 0, 4), ("B", 0, 0, 0))
        route = nav.route("lobby", "lab")
        self.assertEqual(route[0]["building"], "A")
        self.assertEqual(route[-1]["building"], "B")
        self.assertIn("skybridge", [step["via"] for step in route])

    def test_3d_map_is_serializable(self):
        nav = IndoorMap3D()
        nav.add_floor("A", 1, [[1, 1], [1, "#"]], cell_size=2, floor_height=4)
        result = nav.to_3d()
        self.assertEqual(len(result["nodes"]), 3)
        self.assertEqual(result["nodes"][0]["position"]["z"], 4)


if __name__ == "__main__":
    unittest.main()
