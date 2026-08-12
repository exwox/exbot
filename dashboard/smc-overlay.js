(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.XBotSMC = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function finite(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
    }

    function detectStructure(candles, pivotLength = 2) {
        const size = Math.max(1, Math.trunc(Number(pivotLength) || 2));
        const swings = [];
        let previousHigh = null;
        let previousLow = null;

        for (let index = size; index < candles.length - size; index += 1) {
            const candle = candles[index];
            const high = finite(candle.high);
            const low = finite(candle.low);
            const left = candles.slice(index - size, index);
            const right = candles.slice(index + 1, index + size + 1);
            const pivotHigh = left.every(item => high > finite(item.high))
                && right.every(item => high >= finite(item.high));
            const pivotLow = left.every(item => low < finite(item.low))
                && right.every(item => low <= finite(item.low));

            if (pivotHigh) {
                const label = previousHigh === null || high > previousHigh ? 'HH' : 'LH';
                swings.push({ index, price: high, kind: 'high', label });
                previousHigh = high;
            }
            if (pivotLow) {
                const label = previousLow === null || low > previousLow ? 'HL' : 'LL';
                swings.push({ index, price: low, kind: 'low', label });
                previousLow = low;
            }
        }

        swings.sort((a, b) => a.index - b.index || (a.kind === 'high' ? -1 : 1));
        const breaks = [];
        let activeHigh = null;
        let activeLow = null;
        let brokenHighIndex = -1;
        let brokenLowIndex = -1;
        let trend = 'neutral';

        for (let index = 0; index < candles.length; index += 1) {
            for (const swing of swings.filter(item => item.index === index)) {
                if (swing.kind === 'high') activeHigh = swing;
                else activeLow = swing;
            }
            const close = finite(candles[index].close);
            if (activeHigh && activeHigh.index > brokenHighIndex && close > activeHigh.price) {
                breaks.push({
                    index, fromIndex: activeHigh.index, price: activeHigh.price,
                    direction: 'bullish', label: trend === 'bearish' ? 'CHoCH' : 'BOS'
                });
                brokenHighIndex = activeHigh.index;
                trend = 'bullish';
            }
            if (activeLow && activeLow.index > brokenLowIndex && close < activeLow.price) {
                breaks.push({
                    index, fromIndex: activeLow.index, price: activeLow.price,
                    direction: 'bearish', label: trend === 'bullish' ? 'CHoCH' : 'BOS'
                });
                brokenLowIndex = activeLow.index;
                trend = 'bearish';
            }
        }
        return { swings, breaks, trend };
    }

    function plotlyOverlay(candles, timestamps, options = {}) {
        const structure = detectStructure(candles, options.pivotLength || 2);
        const annotations = structure.swings.map(swing => ({
            x: timestamps[swing.index], y: swing.price, xref: 'x', yref: 'y',
            text: `<b>${swing.label}</b>`, showarrow: false,
            yshift: swing.kind === 'high' ? 13 : -13,
            font: {
                size: 10,
                color: swing.kind === 'high' ? '#ffb74d' : '#4dd0e1'
            },
            bgcolor: 'rgba(19,23,34,.82)', borderpad: 2
        }));
        const shapes = [];
        structure.breaks.forEach(event => {
            const color = event.direction === 'bullish' ? '#00c853' : '#ff5252';
            shapes.push({
                type: 'line', xref: 'x', yref: 'y',
                x0: timestamps[event.fromIndex], x1: timestamps[event.index],
                y0: event.price, y1: event.price,
                line: { color, width: 1.4, dash: event.label === 'CHoCH' ? 'dash' : 'dot' }
            });
            annotations.push({
                x: timestamps[event.index], y: event.price, xref: 'x', yref: 'y',
                text: `<b>${event.label}</b>`, showarrow: true, arrowhead: 0,
                ax: 0, ay: event.direction === 'bullish' ? 24 : -24,
                font: { size: 10, color }, arrowcolor: color,
                bgcolor: 'rgba(19,23,34,.9)', bordercolor: color,
                borderwidth: 1, borderpad: 3
            });
        });
        return { ...structure, annotations, shapes };
    }

    return { detectStructure, plotlyOverlay };
}));
