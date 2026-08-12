'use strict';

const defaults = require('./config/strategy_defaults.json');

function strategyDefaults() {
    return { ...defaults };
}

module.exports = { DEFAULT_STRATEGY: Object.freeze({ ...defaults }), strategyDefaults };
