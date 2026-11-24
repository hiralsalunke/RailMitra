from bokeh.plotting import figure
from bokeh.embed import components
from bokeh.models import ColumnDataSource


def _bar_chart(title, categories, values, color, value_label="Count"):
    """
    Builds one interactive Bokeh bar chart. Hovering over any bar shows the
    exact category + value in a tooltip -- this is the 'extra information'
    Bokeh gives that a static image/Chart.js canvas doesn't.
    """
    source = ColumnDataSource(data=dict(categories=categories, values=values))

    p = figure(
        x_range=categories,
        height=280,
        title=title,
        toolbar_location=None,
        tools="hover",
        tooltips=[("", "@categories"), (value_label, "@values")],
        sizing_mode="stretch_width",
    )
    p.vbar(x="categories", top="values", width=0.6, source=source, color=color)

    p.y_range.start = 0
    p.xgrid.grid_line_color = None
    p.ygrid.grid_line_color = "#1c2d55"
    p.xaxis.major_label_orientation = 0.9

    # theme it to match the dark navy dashboard instead of Bokeh's default light look
    p.background_fill_color = None
    p.border_fill_color = None
    p.outline_line_color = None
    p.title.text_color = "#eaf1ff"
    p.title.text_font = "Space Grotesk, sans-serif"
    p.xaxis.axis_line_color = "#5f8cff"
    p.yaxis.axis_line_color = "#5f8cff"
    p.xaxis.major_label_text_color = "#8ea1c7"
    p.yaxis.major_label_text_color = "#8ea1c7"
    p.xaxis.major_tick_line_color = "#5f8cff"
    p.yaxis.major_tick_line_color = "#5f8cff"

    return p


def build_analytics_charts(data):
    """
    Takes the analytics data dict (from get_analytics_data()) and returns
    a Bokeh <script> tag plus a dict of <div> tags -- one per chart -- to
    embed directly into the analytics.html template.
    """
    charts = {}

    if data["station_counts"]:
        charts["stations"] = _bar_chart(
            "Trains per Station",
            [r["station_name"] for r in data["station_counts"]],
            [r["cnt"] for r in data["station_counts"]],
            "#2f6fed", "Trains"
        )

    if data["conflicts_by_station"]:
        charts["conflicts"] = _bar_chart(
            "Conflicts per Station",
            [r["station_name"] for r in data["conflicts_by_station"]],
            [r["cnt"] for r in data["conflicts_by_station"]],
            "#ffb020", "Conflicts"
        )

    if data["priority_counts"]:
        charts["priority"] = _bar_chart(
            "Priority Breakdown",
            [r["priority"] for r in data["priority_counts"]],
            [r["cnt"] for r in data["priority_counts"]],
            "#ff5a5a", "Trains"
        )

    if data["status_counts"]:
        charts["status"] = _bar_chart(
            "Status Breakdown",
            [r["status"] for r in data["status_counts"]],
            [r["cnt"] for r in data["status_counts"]],
            "#3ddc97", "Trains"
        )

    script, divs = components(charts)
    return script, divs