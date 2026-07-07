# Implementation Plan: Khaya Frontend Integration

## Overview

Add Khaya AI as an alternative transcription provider in the frontend. This involves adding a provider selector, a dynamic language picker, a separate transcription handler for Khaya, provider-aware form validation, and history integration — all within the existing vanilla JS architecture (`frontend/index.html` and `frontend/main.js`).

## Tasks

- [x] 1. Add Provider Selector HTML and CSS
  - [x] 1.1 Add provider radio buttons to `frontend/index.html`
    - Insert a new `controls-section` div with id `providerSection` as the first controls section in the left sidebar (before audio source selection)
    - Add two `dg-card--selectable` radio buttons: "Deepgram" (checked by default) and "Khaya AI"
    - Use Font Awesome icons: `fa-waveform-lines` for Deepgram, `fa-language` for Khaya AI
    - _Requirements: 1.1, 1.2, 6.1, 6.3, 6.4_

  - [x] 1.2 Add Language Picker HTML to `frontend/index.html`
    - Insert a new `controls-section` div with id `languageSection` (hidden by default with `style="display: none;"`)
    - Add a `<select>` with id `khayaLanguage` using `dg-input` class, initially disabled with "Loading languages..." placeholder
    - _Requirements: 2.2, 2.6, 6.2, 6.3_

  - [x] 1.3 Add `id="modelSection"` to the existing Model controls-section div
    - The existing model `<select>` container needs an id so it can be toggled by the provider switch handler
    - _Requirements: 1.4, 1.5_

  - [x] 1.4 Add `.provider-selector` CSS to the `<style>` block in `frontend/index.html`
    - Add `display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem;` for the provider selector grid
    - _Requirements: 6.1_

- [x] 2. Implement Provider Switch Logic in `frontend/main.js`
  - [x] 2.1 Add `KHAYA_LANGUAGES_ENDPOINT` constant and `getSelectedProvider()` utility function
    - Add `const KHAYA_LANGUAGES_ENDPOINT = "api/khaya/languages";` in the configuration section
    - Add `let khayaLanguagesLoaded = false;` state variable
    - Add `getSelectedProvider()` that reads the checked provider radio value
    - _Requirements: 1.1, 2.1_

  - [x] 2.2 Implement `handleProviderChange()` function
    - When "khaya" is selected: show `languageSection`, hide `modelSection`, disable URL audio source cards, call `fetchKhayaLanguages()`
    - When "deepgram" is selected: hide `languageSection`, show `modelSection`, re-enable URL audio source cards
    - Call `updateFormValidation()` at the end
    - _Requirements: 1.3, 1.4, 1.5, 4.3, 4.4_

  - [x] 2.3 Register provider radio change listeners in `setupEventListeners()`
    - Add event listeners for `input[name="provider"]` radio buttons calling `handleProviderChange`
    - _Requirements: 1.3_

- [x] 3. Implement Language Fetcher in `frontend/main.js`
  - [x] 3.1 Implement `fetchKhayaLanguages()` async function
    - Fetch from `KHAYA_LANGUAGES_ENDPOINT` (no auth required)
    - On success: populate `khayaLanguage` select with `<option>` elements using `lang.code` as value and `lang.name` as display text, enable the select, set `khayaLanguagesLoaded = true`
    - On error: show error message via `showError()`, display "Failed to load languages" in the select
    - While loading: keep select disabled with "Loading languages..." text
    - Skip fetch if `khayaLanguagesLoaded` is already true
    - Call `updateFormValidation()` after loading completes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 4. Implement Khaya Transcription Flow in `frontend/main.js`
  - [x] 4.1 Implement `handleKhayaTranscribe()` async function
    - Validate that a file is uploaded and a language is selected
    - Build FormData with `file` and `language` fields
    - Call `authenticatedFetch("api/khaya/transcription", ...)` with POST method
    - On success: save to history (with provider "khaya-ai"), display transcript and metadata
    - On error: display error message via `showError()`
    - Show processing indicator via `showWorking()` while request is in flight
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 4.2 Modify existing `handleTranscribe()` to dispatch by provider
    - Add provider check at the top: if `getSelectedProvider() === "khaya"`, call `handleKhayaTranscribe()` and return early
    - Existing Deepgram logic remains unchanged
    - _Requirements: 3.1_

- [x] 5. Update Form Validation in `frontend/main.js`
  - [x] 5.1 Modify `isFormValid()` to be provider-aware
    - When provider is "khaya": require both a file upload AND a selected language (non-empty value)
    - When provider is "deepgram": keep existing logic (radio selected OR file uploaded)
    - _Requirements: 4.1, 4.2_

- [x] 6. Checkpoint - Verify provider switching, language loading, and transcription
  - Ensure all provider switching UI works correctly, languages load from the endpoint, and Khaya transcription submits and displays results. Ask the user if questions arise.

- [x] 7. Update History Integration in `frontend/main.js`
  - [x] 7.1 Modify `saveTranscriptionToHistory()` to accept a `provider` parameter
    - Add optional fourth parameter `provider = "deepgram"`
    - Include `provider` field in the history entry object
    - _Requirements: 5.1_

  - [x] 7.2 Update `renderHistory()` to display provider name in history items
    - Show "Khaya AI" for entries with `provider === "khaya-ai"`, otherwise show the model name
    - _Requirements: 5.2_

  - [x] 7.3 Ensure `loadHistoryEntry()` correctly displays Khaya AI history entries
    - Khaya AI entries display transcript and metadata (including language) when loaded
    - _Requirements: 5.3_

- [x] 8. Final checkpoint - Full integration verification
  - Ensure provider selector defaults to Deepgram, switching to Khaya AI shows language picker and hides model select, URL cards are disabled for Khaya, form validation works per provider, transcription succeeds for both providers, and history entries display correctly with provider identification. Ask the user if questions arise.

## Notes

- No property-based tests — this is UI wiring with no pure algorithmic logic
- All changes are in `frontend/index.html` and `frontend/main.js` only
- No new dependencies or build steps needed
- Backend endpoints (`/api/khaya/languages`, `/api/khaya/transcription`) already exist
- Uses existing patterns: `dg-*` classes, `authenticatedFetch()`, `showError()`/`showWorking()`/`hideStatus()`
