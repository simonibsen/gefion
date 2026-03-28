// Shared D3 chart utilities — tooltip, crosshair, zoom, formatting

function createTooltip(container) {
    const tooltip = container.append("div").attr("class", "tooltip");
    return {
        show(html, x, y) {
            tooltip.html(html)
                .classed("visible", true)
                .style("left", (x + 15) + "px")
                .style("top", (y - 10) + "px");
        },
        hide() { tooltip.classed("visible", false); },
        el: tooltip
    };
}

function createCrosshair(svg, width, height) {
    const g = svg.append("g").attr("class", "crosshair").style("display", "none");
    const vLine = g.append("line").attr("y1", 0).attr("y2", height).attr("stroke", "rgba(128,128,128,0.4)").attr("stroke-dasharray", "3,3");
    const hLine = g.append("line").attr("x1", 0).attr("x2", width).attr("stroke", "rgba(128,128,128,0.4)").attr("stroke-dasharray", "3,3");
    return {
        show(x, y) { g.style("display", null); vLine.attr("x1", x).attr("x2", x); hLine.attr("y1", y).attr("y2", y); },
        hide() { g.style("display", "none"); },
        g
    };
}

function formatPrice(v) {
    if (v == null) return "-";
    return v >= 1000 ? d3.format(",.0f")(v) : v >= 1 ? d3.format(",.2f")(v) : d3.format(",.4f")(v);
}

function formatDate(d) {
    const dt = new Date(d);
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return months[dt.getMonth()] + " " + dt.getDate() + ", " + dt.getFullYear();
}

function formatVolume(v) {
    if (v >= 1e9) return (v/1e9).toFixed(1) + "B";
    if (v >= 1e6) return (v/1e6).toFixed(1) + "M";
    if (v >= 1e3) return (v/1e3).toFixed(1) + "K";
    return v.toString();
}

function addZoomBehavior(svg, xScale, xAxis, redrawFn) {
    const zoom = d3.zoom()
        .scaleExtent([0.5, 10])
        .on("zoom", function(event) {
            const newX = event.transform.rescaleX(xScale);
            xAxis.call(d3.axisBottom(newX));
            redrawFn(newX);
        });
    svg.call(zoom);
    return zoom;
}

function responsiveResize(container, drawFn) {
    const observer = new ResizeObserver(() => drawFn());
    observer.observe(container.node());
    return observer;
}
