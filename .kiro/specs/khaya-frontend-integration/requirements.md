# Requirements Document

## Introduction

This feature adds Khaya AI transcription support to the frontend UI, allowing users to choose between Deepgram and Khaya AI as their transcription provider. When Khaya AI is selected, users can pick from supported African languages (Twi, Ewe, Ga, Dagbani, etc.) fetched dynamically from the backend. The feature integrates with the existing `/api/khaya/transcription` and `/api/khaya/languages` endpoints alongside the current Deepgram flow.

## Glossary

- **Provider_Selector**: A UI control that allows users to choose between Deepgram and Khaya AI transcription services
- **Language_Picker**: A dropdown/select control that displays available Khaya AI languages and allows the user to choose one
- **Frontend_App**: The vanilla JavaScript frontend application (`frontend/main.js` and `frontend/index.html`)
- **Khaya_Languages_Endpoint**: The `GET /api/khaya/languages` backend endpoint that returns supported languages
- **Khaya_Transcription_Endpoint**: The `POST /api/khaya/transcription` backend endpoint that performs Khaya AI transcription
- **Deepgram_Transcription_Endpoint**: The existing `POST /api/transcription` backend endpoint for Deepgram transcription
- **Authenticated_Fetch**: The existing `authenticatedFetch()` wrapper that adds JWT Authorization headers to requests

## Requirements

### Requirement 1: Provider Selection

**User Story:** As a user, I want to choose between Deepgram and Khaya AI transcription providers, so that I can use the service best suited for my audio content.

#### Acceptance Criteria

1. THE Frontend_App SHALL display a Provider_Selector control in the left sidebar controls section with two options: "Deepgram" and "Khaya AI"
2. THE Provider_Selector SHALL default to "Deepgram" as the selected provider
3. WHEN the user selects "Khaya AI" in the Provider_Selector, THE Frontend_App SHALL display the Language_Picker control
4. WHEN the user selects "Deepgram" in the Provider_Selector, THE Frontend_App SHALL hide the Language_Picker control
5. WHEN the user selects "Deepgram" in the Provider_Selector, THE Frontend_App SHALL display the existing Model select dropdown

### Requirement 2: Language Picker for Khaya AI

**User Story:** As a user, I want to select from available African languages when using Khaya AI, so that my audio is transcribed in the correct language.

#### Acceptance Criteria

1. WHEN "Khaya AI" is selected in the Provider_Selector, THE Frontend_App SHALL fetch the list of supported languages from the Khaya_Languages_Endpoint
2. THE Language_Picker SHALL display each language with its human-readable name (e.g., "Asante Twi", "Ewe", "Dagbani")
3. THE Language_Picker SHALL store the language code (e.g., "tw", "ee", "dag") as the value for each option
4. THE Language_Picker SHALL select the first available language by default after loading
5. IF the Khaya_Languages_Endpoint returns an error, THEN THE Frontend_App SHALL display an error message to the user and disable the Language_Picker
6. WHILE the languages are being fetched, THE Language_Picker SHALL display a loading state indicating that languages are being retrieved

### Requirement 3: Khaya AI Transcription Flow

**User Story:** As a user, I want to transcribe audio using Khaya AI, so that I can get transcriptions for African language audio content.

#### Acceptance Criteria

1. WHEN "Khaya AI" is selected and the user clicks transcribe, THE Frontend_App SHALL send a multipart form request to the Khaya_Transcription_Endpoint with the audio file and selected language code
2. THE Frontend_App SHALL use the Authenticated_Fetch wrapper when calling the Khaya_Transcription_Endpoint
3. WHEN the Khaya_Transcription_Endpoint returns a successful response, THE Frontend_App SHALL display the transcript text in the main content area
4. WHEN the Khaya_Transcription_Endpoint returns a successful response, THE Frontend_App SHALL save the result to the transcription history in localStorage
5. IF the Khaya_Transcription_Endpoint returns an error, THEN THE Frontend_App SHALL display the error message to the user
6. THE Frontend_App SHALL display a processing indicator while the Khaya AI transcription request is in flight

### Requirement 4: Provider-Aware Form Validation

**User Story:** As a user, I want clear feedback on what inputs are required for each provider, so that I can submit valid transcription requests.

#### Acceptance Criteria

1. WHEN "Khaya AI" is selected in the Provider_Selector, THE Frontend_App SHALL require both an audio file and a selected language before enabling the transcribe button
2. WHEN "Deepgram" is selected in the Provider_Selector, THE Frontend_App SHALL require either an audio source URL or an uploaded file before enabling the transcribe button
3. WHEN "Khaya AI" is selected, THE Frontend_App SHALL disable the pre-defined audio URL radio buttons since Khaya AI only accepts file uploads
4. WHEN "Deepgram" is selected after switching from "Khaya AI", THE Frontend_App SHALL re-enable the pre-defined audio URL radio buttons

### Requirement 5: History Integration

**User Story:** As a user, I want my Khaya AI transcription results saved alongside Deepgram results, so that I can review all past transcriptions in one place.

#### Acceptance Criteria

1. WHEN a Khaya AI transcription completes successfully, THE Frontend_App SHALL save the result to localStorage history with the provider identified as "khaya-ai"
2. THE Frontend_App SHALL display the provider name in the history sidebar items so users can distinguish between Deepgram and Khaya AI results
3. WHEN a Khaya AI history entry is loaded, THE Frontend_App SHALL display the transcript and metadata including the language used

### Requirement 6: UI Consistency and Styling

**User Story:** As a user, I want the Khaya AI controls to match the existing interface style, so that the experience feels cohesive.

#### Acceptance Criteria

1. THE Provider_Selector SHALL use Deepgram Design System classes (dg-*) consistent with existing form controls in the sidebar
2. THE Language_Picker SHALL use the `dg-input` class matching the existing Model select dropdown styling
3. THE Provider_Selector and Language_Picker SHALL be placed in a `controls-section` div following the existing layout pattern
4. THE Frontend_App SHALL use Font Awesome icons for the provider options where appropriate
