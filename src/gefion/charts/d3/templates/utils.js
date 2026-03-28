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

// --- Animation utilities (inspired by lens-of-power viewer) ---

function animateEntry(selection, duration) {
    // Fade + slide up on initial render
    duration = duration || 400;
    selection
        .style("opacity", 0)
        .attr("transform", function() {
            const current = d3.select(this).attr("transform") || "";
            return current + " translate(0, 8)";
        })
        .transition()
        .duration(duration)
        .ease(d3.easeCubicOut)
        .style("opacity", 1)
        .attr("transform", function() {
            const current = d3.select(this).attr("data-base-transform") || "";
            return current;
        });
    return selection;
}

function staggeredEntry(selection, duration, delay) {
    // Staggered fade-in for grouped elements (bars, dots, etc.)
    duration = duration || 300;
    delay = delay || 30;
    selection.each(function(d, i) {
        d3.select(this)
            .style("opacity", 0)
            .transition()
            .delay(i * delay)
            .duration(duration)
            .ease(d3.easeCubicOut)
            .style("opacity", 1);
    });
    return selection;
}

function hoverHighlight(selection, opts) {
    // Add hover effect: brighten on hover, dim siblings
    opts = opts || {};
    const hoverOpacity = opts.hoverOpacity || 1;
    const dimOpacity = opts.dimOpacity || 0.2;
    const parentSelector = opts.parent || null;

    selection
        .style("cursor", "pointer")
        .on("mouseenter.highlight", function() {
            const self = this;
            const siblings = parentSelector
                ? d3.select(this.closest(parentSelector)).selectAll(selection.node().tagName)
                : selection;
            siblings.transition().duration(150).style("opacity", function() {
                return this === self ? hoverOpacity : dimOpacity;
            });
        })
        .on("mouseleave.highlight", function() {
            const siblings = parentSelector
                ? d3.select(this.closest(parentSelector)).selectAll(selection.node().tagName)
                : selection;
            siblings.transition().duration(300).style("opacity", 1);
        });
    return selection;
}

function pulseElement(selection, duration) {
    // Subtle pulse animation for emphasis
    duration = duration || 1500;
    selection
        .transition()
        .duration(duration / 2)
        .ease(d3.easeSinInOut)
        .attr("r", function() { return +d3.select(this).attr("r") * 1.3; })
        .transition()
        .duration(duration / 2)
        .ease(d3.easeSinInOut)
        .attr("r", function() { return +d3.select(this).attr("r") / 1.3; });
    return selection;
}

function formatPercent(v) {
    if (v == null) return "-";
    return (v >= 0 ? "+" : "") + d3.format(".1f")(v) + "%";
}

function formatCompact(v) {
    if (v == null) return "-";
    if (Math.abs(v) >= 1e9) return d3.format(".1f")(v/1e9) + "B";
    if (Math.abs(v) >= 1e6) return d3.format(".1f")(v/1e6) + "M";
    if (Math.abs(v) >= 1e3) return d3.format(".1f")(v/1e3) + "K";
    return d3.format(".1f")(v);
}
