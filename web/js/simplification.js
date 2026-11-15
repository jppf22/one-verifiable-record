/**
 * Simplification Mode Toggle UI
 * 
 * Provides a user interface for selecting and viewing the 5 simplification modes:
 * 1. Minimal Mode - Core comparison only
 * 2. Read-Only Mode - Disable all write operations
 * 3. Single-Language Mode - Focus on one language
 * 4. Deterministic Mode - Skip LLM, use only deterministic verdicts
 * 5. No-Extension Mode - Remove extension functionality
 */

import { api } from './api.js';
import { $, $$, el, toast } from './util.js';

// Mode descriptions for display
const MODE_INFO = {
    none: {
        name: 'Full Mode',
        icon: '⭐',
        description: 'All features enabled',
        color: '#44cc44',
        short: 'Full'
    },
    minimal: {
        name: 'Minimal Mode',
        icon: '⚡',
        description: 'Core comparison only',
        color: '#ffaa44',
        short: 'Minimal'
    },
    read_only: {
        name: 'Read-Only Mode',
        icon: '🔒',
        description: 'No write operations',
        color: '#4488ff',
        short: 'Read-Only'
    },
    single_language: {
        name: 'Single-Language Mode',
        icon: '🌐',
        description: 'One language only',
        color: '#aa44ff',
        short: 'Single-Lang'
    },
    deterministic: {
        name: 'Deterministic Mode',
        icon: '📊',
        description: 'No LLM, deterministic only',
        color: '#ff4488',
        short: 'Deterministic'
    },
    no_extension: {
        name: 'No-Extension Mode',
        icon: '🚫',
        description: 'Extension features disabled',
        color: '#888888',
        short: 'No-Extension'
    }
};

// Current state
let currentMode = null;
let simplificationConfig = null;

/**
 * Fetch current simplification configuration from server
 */
async function fetchSimplificationConfig() {
    try {
        const config = await api('/simplification');
        simplificationConfig = config;
        currentMode = config.current_mode;
        return config;
    } catch (error) {
        console.error('Failed to fetch simplification config:', error);
        return null;
    }
}

/**
 * Fetch available simplification options
 */
async function fetchSimplificationOptions() {
    try {
        return await api('/simplification/options');
    } catch (error) {
        console.error('Failed to fetch simplification options:', error);
        return null;
    }
}

/**
 * Set simplification mode
 */
async function setSimplificationMode(mode, singleLanguage = null) {
    try {
        const response = await api('/simplification', {
            method: 'POST',
            body: JSON.stringify({
                mode: mode,
                single_language: singleLanguage
            })
        });
        currentMode = mode;
        toast(`Simplification mode set to: ${MODE_INFO[mode]?.name || mode}`, 'info', 5000);
        return response;
    } catch (error) {
        console.error('Failed to set simplification mode:', error);
        toast('Failed to set simplification mode. May require server restart.', 'error', 5000);
        throw error;
    }
}

/**
 * Create mode option element
 */
function createModeOption(modeId, modeConfig, isActive) {
    const info = MODE_INFO[modeId];
    const container = el('label', {
        className: `simp-option${isActive ? ' active' : ''}`,
        style: `border-left: 4px solid ${info.color}`
    }, [
        el('input', {
            type: 'radio',
            name: 'simplification-mode',
            value: modeId,
            checked: isActive
        }),
        el('span', { className: 'simp-option-icon' }, info.icon),
        el('div', { className: 'simp-option-content' }, [
            el('strong', { className: 'simp-option-name' }, info.name),
            el('small', { className: 'simp-option-desc' }, modeConfig.description || info.description)
        ])
    ]);
    
    // Add click handler
    container.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return; // Let the input handle it
        const radio = container.querySelector('input');
        radio.click();
    });
    
    // Handle selection
    const radio = container.querySelector('input');
    radio.addEventListener('change', () => {
        selectMode(modeId);
    });
    
    return container;
}

/**
 * Handle mode selection
 */
async function selectMode(modeId) {
    // For single_language mode, prompt for language
    if (modeId === 'single_language') {
        const lang = prompt('Enter language code (fi, sv, en):', 'en');
        if (!lang || !['fi', 'sv', 'en'].includes(lang)) {
            toast('Please enter a valid language code: fi, sv, or en', 'error', 3000);
            return;
        }
        await setSimplificationMode(modeId, lang);
    } else {
        await setSimplificationMode(modeId);
    }
    
    // Refresh the UI
    await refreshSimplificationUI();
    
    // Close menu
    hideSimplificationMenu();
}

/**
 * Refresh the simplification UI
 */
async function refreshSimplificationUI() {
    const config = await fetchSimplificationConfig();
    if (!config) return;
    
    currentMode = config.current_mode;
    
    // Update button
    const btn = $('#simp-toggle-btn');
    const label = btn?.querySelector('.simp-label');
    const icon = btn?.querySelector('.simp-icon');
    
    if (btn && label && icon) {
        const info = MODE_INFO[currentMode] || MODE_INFO.none;
        label.textContent = info.short;
        icon.textContent = info.icon;
        btn.title = `${info.name}: ${info.description}`;
        btn.style.setProperty('--simp-color', info.color);
    }
    
    // Update menu options
    const optionsContainer = $('#simp-options');
    if (!optionsContainer) return;
    
    optionsContainer.innerHTML = '';
    
    // Create options for each mode
    for (const [modeId, modeConfig] of Object.entries(config.modes)) {
        const option = createModeOption(modeId, modeConfig, modeId === currentMode);
        optionsContainer.appendChild(option);
    }
    
    // Add current mode indicator
    const currentModeInfo = MODE_INFO[currentMode] || MODE_INFO.none;
    const currentIndicator = el('div', {
        className: 'simp-current-indicator',
        style: `background-color: ${currentModeInfo.color}`
    }, [
        el('span', {}, `${currentModeInfo.icon} `),
        el('strong', {}, currentModeInfo.name),
        el('span', {}, ` is active`)
    ]);
    
    optionsContainer.prepend(currentIndicator);
}

/**
 * Show simplification menu
 */
function showSimplificationMenu() {
    const menu = $('#simp-menu');
    const btn = $('#simp-toggle-btn');
    
    if (!menu || !btn) return;
    
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    
    // Position menu
    const rect = btn.getBoundingClientRect();
    menu.style.position = 'absolute';
    menu.style.right = '0';
    menu.style.top = `${rect.bottom + window.scrollY + 5}px`;
    menu.style.minWidth = `${rect.width}px`;
}

/**
 * Hide simplification menu
 */
function hideSimplificationMenu() {
    const menu = $('#simp-menu');
    const btn = $('#simp-toggle-btn');
    
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
}

/**
 * Toggle simplification menu
 */
function toggleSimplificationMenu() {
    const menu = $('#simp-menu');
    if (menu.hidden) {
        showSimplificationMenu();
    } else {
        hideSimplificationMenu();
    }
}

/**
 * Initialize simplification UI
 */
export async function initSimplificationUI() {
    // Check if toggle should be shown (only for admin/debug users for now)
    const toggle = $('#simplification-toggle');
    const btn = $('#simp-toggle-btn');
    const menu = $('#simp-menu');
    
    if (!toggle || !btn || !menu) {
        console.log('Simplification UI elements not found');
        return;
    }
    
    // Show the toggle
    toggle.hidden = false;
    
    // Add click handler for button
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleSimplificationMenu();
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', (e) => {
        if (!menu.contains(e.target) && e.target !== btn) {
            hideSimplificationMenu();
        }
    });
    
    // Close menu when pressing escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideSimplificationMenu();
        }
    });
    
    // Initialize UI with current config
    await refreshSimplificationUI();
    
    // Fetch options for detailed info
    const options = await fetchSimplificationOptions();
    console.log('Simplification options:', options);
}

/**
 * Get current simplification mode
 */
export function getCurrentMode() {
    return currentMode;
}

/**
 * Get full simplification config
 */
export function getSimplificationConfig() {
    return simplificationConfig;
}

/**
 * Check if a feature is blocked by current simplification mode
 */
export function isFeatureBlocked(feature) {
    if (!simplificationConfig) return false;
    
    const mode = simplificationConfig.current_mode;
    const modes = simplificationConfig.modes || {};
    const currentModeConfig = modes[mode] || {};
    
    if (!currentModeConfig.blocks) return false;
    
    return currentModeConfig.blocks.includes(feature);
}

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', initSimplificationUI);
