from django.test import SimpleTestCase

from workspace.common.charts import column_chart


def _series(name="Done", css_class="fill-success", values=(1, 2)):
    return [{"name": name, "css_class": css_class, "values": list(values)}]


class ColumnChartTests(SimpleTestCase):
    def test_one_bar_per_value_per_series(self):
        chart = column_chart(
            ["W1", "W2"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1, 2]},
                {"name": "Completed", "css_class": "fill-success", "values": [3, 4]},
            ],
        )
        self.assertEqual(len(chart["bars"]), 4)

    def test_axis_top_is_rounded_up_to_a_whole_gridline_step(self):
        # Raw max 5 over 4 gridlines rounds the step up to 2, so the axis
        # tops out at 8 and every tick label is a whole number.
        chart = column_chart(["W1"], _series(values=(5,)))
        self.assertEqual(chart["max"], 8)
        self.assertEqual([g["value"] for g in chart["gridlines"]], [0, 2, 4, 6, 8])

    def test_all_zero_series_does_not_divide_by_zero(self):
        chart = column_chart(["W1", "W2"], _series(values=(0, 0)))
        self.assertEqual(chart["max"], 4)
        self.assertEqual([bar["height"] for bar in chart["bars"]], ["0.00", "0.00"])

    def test_tallest_bar_fills_the_plot_area(self):
        chart = column_chart(["W1"], _series(values=(4,)))
        self.assertEqual(chart["bars"][0]["height"], chart["plot"]["height"])
        self.assertEqual(chart["bars"][0]["y"], chart["plot"]["y"])

    def test_coordinates_are_strings_so_templates_cannot_localize_them(self):
        # A raw float renders as "12,5" under a comma-decimal locale, which
        # silently corrupts the SVG geometry.
        chart = column_chart(["W1"], _series(values=(3,)))
        bar = chart["bars"][0]
        for key in ("x", "y", "width", "height"):
            self.assertIsInstance(bar[key], str)
        self.assertIsInstance(chart["max"], int)
        self.assertIsInstance(chart["gridlines"][0]["value"], int)

    def test_bars_of_a_series_share_a_width_and_advance_left_to_right(self):
        chart = column_chart(["W1", "W2", "W3"], _series(values=(1, 1, 1)))
        widths = {bar["width"] for bar in chart["bars"]}
        self.assertEqual(len(widths), 1)
        xs = [float(bar["x"]) for bar in chart["bars"]]
        self.assertEqual(xs, sorted(xs))

    def test_series_share_a_slot_without_overlapping(self):
        chart = column_chart(
            ["W1"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1]},
                {"name": "Completed", "css_class": "fill-success", "values": [1]},
            ],
        )
        first, second = chart["bars"]
        self.assertLessEqual(
            float(first["x"]) + float(first["width"]), float(second["x"]) + 0.01
        )

    def test_category_labels_are_centred_on_their_slot(self):
        chart = column_chart(["W1", "W2"], _series(values=(1, 1)))
        first, second = chart["categories"]
        self.assertEqual(first["label"], "W1")
        self.assertLess(float(first["x"]), float(second["x"]))

    def test_empty_input_renders_an_empty_chart_without_raising(self):
        chart = column_chart([], [])
        self.assertEqual(chart["bars"], [])
        self.assertEqual(chart["categories"], [])
        self.assertEqual(chart["series"], [])
        self.assertEqual(chart["max"], 4)

    def test_legend_carries_each_series_name_and_colour(self):
        chart = column_chart(
            ["W1"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1]},
                {"name": "Completed", "css_class": "fill-success", "values": [1]},
            ],
        )
        self.assertEqual(
            chart["series"],
            [
                {"name": "Created", "css_class": "fill-accent"},
                {"name": "Completed", "css_class": "fill-success"},
            ],
        )

    def test_bar_tooltip_names_its_category_and_series(self):
        chart = column_chart(["Mar 03"], _series(name="Completed", values=(7,)))
        self.assertEqual(chart["bars"][0]["tooltip"], "Mar 03: 7 completed")
