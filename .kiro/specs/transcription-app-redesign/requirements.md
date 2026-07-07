# Requirements Document

## Introduction

This feature redesigns the existing single-page AI transcription demo into a clean, modern, multi-page web application built to demonstrate and test transcription functionality. The redesign transforms the current three-column single-view interface into a routed multi-page application (Landing, Transcribe, Projects, History, About, and Error pages) with reusable components and a maintainable folder structure.

The application is explicitly a demonstration and testing surface for an AI transcription backend, not a production SaaS product. There is no authentication UI, billing, account management, subscription handling, or API management surface. The existing backend session-auth flow (JWT via `GET /api/session`) remains transparent to the UI.

All existing transcription functionality MUST be preserved, including the two providers (Deepgram and Khaya AI), the JWT session auth flow, LocalStorage-based history with deep-linking, and transcript/metadata display.

The design applies Material Design 3 principles (as documented in `.kiro/steering/material3-design.md`) layered over the existing Deepgram `dg-*` design system, supports light and dark modes, and uses a lightweight animation library appropriate for the vanilla-JS/Vite stack (with reduced-motion fallbacks). The stack remains vanilla JavaScript with Vite; no UI frameworks are introduced.

## Glossary

- **Transcription_App**: The complete redesigned multi-page web application, encompassing all pages, components, and client-side logic.
- **Router**: The client-side routing component responsible for mapping URL paths to pages, rendering the correct page, and managing navigation.
- **Navigation_Bar**: The global responsive navigation component present across pages (logo, page links, active-page highlight, mobile hamburger menu).
- **Landing_Page**: The marketing/overview page presenting the hero, feature cards, "How It Works," sample output, and footer.
- **Transcribe_Page**: The core workspace page for uploading audio, configuring options, running transcription, and viewing results.
- **Projects_Page**: The page listing saved transcription projects as cards with search, sort, filter, and management actions.
- **History_Page**: The page listing previous transcription jobs with search, filter, sort, and clear actions.
- **About_Page**: The page describing the application overview, feature summary, technology stack, and future improvements.
- **Error_Page**: Any of the dedicated error surfaces (404 Not Found, Processing Error, Unsupported File Format).
- **Deepgram_Provider**: The transcription pathway using the Deepgram backend endpoint `POST /api/transcription` with model selection (nova-3) and sample-URL or file-upload input.
- **Khaya_Provider**: The transcription pathway using the Khaya AI backend endpoint `POST /api/khaya/transcription` with file-upload input and African-language selection.
- **Session_Auth**: The transparent JWT session flow using `GET /api/session` and the `authenticatedFetch` wrapper that attaches the Bearer token.
- **Transcription_History**: The LocalStorage-persisted list of past transcription results (key `deepgram_transcription_history`, maximum 50 entries).
- **Upload_Zone**: The drag-and-drop and file-picker component on the Transcribe_Page that accepts audio files.
- **Transcript_Viewer**: The component that displays and allows editing of a transcript, including speaker labels, timestamps, confidence scores, search, and playback controls.
- **Component_Library**: The reusable set of UI components (cards, buttons, forms, tabs, tables, tooltips, toasts, progress indicators, skeleton loaders, empty states).
- **Design_System**: The combined Material Design 3 token set and Deepgram `dg-*` classes governing color, typography, shape, motion, and elevation.
- **Animation_Layer**: The lightweight animation library integration (e.g., Motion One or equivalent) plus reduced-motion handling.
- **Theme_Controller**: The component that manages light-mode and dark-mode selection and persistence.
- **Supported_Audio_Format**: An audio input in one of MP3, WAV, M4A, FLAC, OGG, or MP4 (audio extraction).
- **Export_Formatter**: The component that converts a transcript into a downloadable format (TXT, DOCX, PDF, SRT, VTT).

## Requirements

### Requirement 1: Preserve Existing Transcription Functionality

**User Story:** As a developer testing the transcription service, I want all existing transcription capabilities to keep working after the redesign, so that no functionality is lost during the UX overhaul.

#### Acceptance Criteria

1. THE Transcription_App SHALL support Deepgram_Provider transcription using the endpoint `POST /api/transcription`.
2. WHERE the Deepgram_Provider is selected, THE Transcribe_Page SHALL support both sample-audio-URL input and audio file upload.
3. WHERE the Deepgram_Provider is selected, THE Transcribe_Page SHALL provide model selection with `nova-3` available as a model option.
4. THE Transcription_App SHALL support Khaya_Provider transcription using the endpoint `POST /api/khaya/transcription`.
5. WHERE the Khaya_Provider is selected, THE Transcribe_Page SHALL support audio file upload and language selection for African languages.
6. WHEN the Khaya_Provider is selected, THE Transcribe_Page SHALL retrieve the available language list from `GET /api/khaya/languages`.
7. WHEN the Transcription_App issues a request to a protected backend endpoint, THE Session_Auth SHALL obtain a JWT from `GET /api/session` and attach a Bearer Authorization header.
8. IF a protected backend request returns HTTP 401, THEN THE Session_Auth SHALL clear the cached token and display a message instructing the user to refresh the page.
9. WHEN a transcription completes successfully, THE Transcription_App SHALL store the result in Transcription_History using the LocalStorage key `deepgram_transcription_history`.
10. WHILE the Transcription_History exceeds 50 entries, THE Transcription_App SHALL retain only the 50 most recent entries.
11. WHEN a URL containing a `request_id` query parameter is loaded, THE Transcription_App SHALL load and display the matching Transcription_History entry.
12. WHEN a Transcription_History entry is displayed, THE Transcription_App SHALL highlight that entry as the active item in the history list.
13. WHEN a transcription result is displayed, THE Transcript_Viewer SHALL show the transcript text and associated metadata including duration, word count, request_id, and model name where those values are present in the response.
14. THE Transcription_App SHALL retrieve application metadata from `GET /api/metadata`.

### Requirement 2: Client-Side Routing and Multi-Page Structure

**User Story:** As a user, I want to navigate between distinct pages via clean URLs, so that I can move around the application in an organized and predictable way.

#### Acceptance Criteria

1. THE Router SHALL map distinct URL paths to the Landing_Page, Transcribe_Page, Projects_Page, History_Page, and About_Page.
2. WHEN a user navigates to a defined route, THE Router SHALL render the corresponding page without a full server page reload.
3. WHEN a user activates the browser back or forward control, THE Router SHALL render the page corresponding to the resulting URL.
4. IF a user navigates to a URL path that matches no defined route, THEN THE Router SHALL render the 404 Not Found Error_Page.
5. WHEN the Transcription_App loads a route directly via its URL, THE Router SHALL render the corresponding page for that URL.
6. WHEN a user shares or reloads a URL that includes a `request_id` query parameter, THE Router SHALL render the appropriate page and load the referenced transcription result.

### Requirement 3: Global Navigation

**User Story:** As a user, I want a consistent navigation bar across the application, so that I can reach any page and know where I currently am.

#### Acceptance Criteria

1. THE Navigation_Bar SHALL display links to the Landing_Page (Home), Transcribe_Page, Projects_Page, History_Page, and About_Page, along with a logo.
2. WHEN a page is active, THE Navigation_Bar SHALL visually highlight the navigation link corresponding to that page.
3. WHILE the viewport width is at or below the mobile breakpoint, THE Navigation_Bar SHALL present navigation links behind a hamburger menu control.
4. WHEN a user activates the hamburger menu control, THE Navigation_Bar SHALL toggle the visibility of the navigation links.
5. WHEN a user selects a navigation link, THE Router SHALL navigate to the corresponding page.
6. WHEN a page transition occurs, THE Animation_Layer SHALL apply a page transition using Material Design 3 motion tokens.

### Requirement 4: Landing Page

**User Story:** As a first-time visitor, I want an informative landing page, so that I understand what the application does and can start transcribing quickly.

#### Acceptance Criteria

1. THE Landing_Page SHALL display a hero section containing the headline "Fast, Accurate AI Audio Transcription", a descriptive subheading, a "Start Transcribing" call-to-action, and a "Learn More" call-to-action.
2. WHEN a user activates the "Start Transcribing" call-to-action, THE Router SHALL navigate to the Transcribe_Page.
3. THE Landing_Page SHALL display a visual representing the transcription flow of upload, waveform, AI processing, and transcript.
4. THE Landing_Page SHALL display feature cards covering transcription, speaker identification, timestamps, AI summaries, translation, keyword extraction, noise reduction, subtitle generation, multi-language support, and export.
5. THE Landing_Page SHALL display a three-step "How It Works" section.
6. THE Landing_Page SHALL display a sample transcription output that includes speaker labels, timestamps, confidence indication, and an AI summary.
7. THE Landing_Page SHALL display a footer containing an About link, a GitHub repository link, a Contact reference, a License reference, and a version identifier.

### Requirement 5: Transcription Workspace — Upload and Input

**User Story:** As a user, I want an intuitive upload experience with clear feedback, so that I can provide audio for transcription without confusion.

#### Acceptance Criteria

1. THE Transcribe_Page SHALL provide an Upload_Zone that supports both drag-and-drop and file-picker selection.
2. THE Upload_Zone SHALL accept audio inputs in the Supported_Audio_Format set (MP3, WAV, M4A, FLAC, OGG, and MP4 audio extraction).
3. IF a user provides a file whose format is not in the Supported_Audio_Format set, THEN THE Transcribe_Page SHALL display the Unsupported File Format Error_Page or an equivalent inline error identifying the unsupported format.
4. WHEN a valid audio file is selected, THE Transcribe_Page SHALL display the file name, file size, and an audio preview control.
5. WHEN a valid audio file is selected, THE Transcribe_Page SHALL display a waveform visualization of the audio.
6. WHILE a file is uploading, THE Transcribe_Page SHALL display upload progress.
7. THE Transcribe_Page SHALL provide a left sidebar with shortcuts for New Transcription, Projects, and History.
8. WHEN a user selects a left-sidebar shortcut, THE Router SHALL navigate to the corresponding page or reset the workspace for a new transcription.

### Requirement 6: Transcription Workspace — Controls

**User Story:** As a user, I want to configure transcription options before processing, so that I can test different transcription features.

#### Acceptance Criteria

1. THE Transcribe_Page SHALL provide a language selector control.
2. THE Transcribe_Page SHALL provide toggle controls for speaker detection, timestamps, translation, AI summary, and noise reduction.
3. THE Transcribe_Page SHALL provide a custom vocabulary input control.
4. WHERE a control corresponds to a parameter supported by the selected provider, THE Transcribe_Page SHALL include that control's value in the transcription request.
5. WHERE a control corresponds to a capability not supported by the selected provider, THE Transcribe_Page SHALL indicate that the control is unavailable for the selected provider.

### Requirement 7: Transcription Workspace — Processing Status

**User Story:** As a user, I want visibility into processing progress, so that I know the transcription is working and roughly how long it will take.

#### Acceptance Criteria

1. WHILE a transcription request is being processed, THE Transcribe_Page SHALL display the current processing stage and a progress indicator.
2. WHILE a transcription request is being processed, THE Transcribe_Page SHALL display an estimated time indication.
3. WHERE processing logs are available, THE Transcribe_Page SHALL provide an option to view the processing logs.
4. IF a transcription request fails, THEN THE Transcribe_Page SHALL display the Processing Error Error_Page or an equivalent inline error with a descriptive message and an action to return to the workspace.
5. WHEN a transcription request completes successfully, THE Transcribe_Page SHALL replace the processing status with the transcription result.

### Requirement 8: Transcript Viewer

**User Story:** As a user, I want a full-featured transcript viewer, so that I can read, edit, search, and navigate the transcription.

#### Acceptance Criteria

1. THE Transcript_Viewer SHALL display the transcript text with speaker labels, timestamps, and confidence scores where those values are present in the response.
2. THE Transcript_Viewer SHALL allow the user to edit the transcript text.
3. THE Transcript_Viewer SHALL provide a search control that locates matching text within the transcript.
4. THE Transcript_Viewer SHALL provide a find-and-replace control that replaces matching text within the transcript.
5. WHEN a user activates the copy control, THE Transcript_Viewer SHALL copy the transcript text to the clipboard.
6. WHEN a user activates the download control, THE Export_Formatter SHALL produce a downloadable file of the transcript.
7. THE Transcript_Viewer SHALL provide audio playback controls including play, pause, and seek.
8. WHEN a user activates a timestamp in the transcript, THE Transcript_Viewer SHALL move audio playback to that timestamp.
9. WHILE rendering a transcript longer than 500 segments, THE Transcript_Viewer SHALL virtualize rendering so that only visible segments are mounted in the DOM.

### Requirement 9: Transcription Workspace — Results Sidebar

**User Story:** As a user, I want an at-a-glance summary sidebar for a completed transcription, so that I can review insights and export in multiple formats.

#### Acceptance Criteria

1. WHERE an AI summary is present in the response, THE Transcribe_Page SHALL display the AI summary in the results sidebar.
2. WHERE detected speakers are present in the response, THE Transcribe_Page SHALL display the detected speakers in the results sidebar.
3. WHERE keywords are present in the response, THE Transcribe_Page SHALL display the extracted keywords in the results sidebar.
4. WHERE a detected language is present in the response, THE Transcribe_Page SHALL display the detected language in the results sidebar.
5. THE Transcribe_Page SHALL display file metadata in the results sidebar.
6. THE Transcribe_Page SHALL provide export options for TXT, DOCX, PDF, SRT, and VTT formats.
7. WHEN a user selects an export format, THE Export_Formatter SHALL produce a downloadable file in the selected format.

### Requirement 10: Projects Page

**User Story:** As a user, I want to organize transcriptions into projects, so that I can manage and revisit my work.

#### Acceptance Criteria

1. THE Projects_Page SHALL display each project as a card showing the file name, upload date, duration, processing status, language, and number of speakers.
2. THE Projects_Page SHALL provide search, sort, and filter controls for the project list.
3. WHEN a user enters a search term, THE Projects_Page SHALL display only projects matching the search term.
4. WHEN a user selects a sort option, THE Projects_Page SHALL order the project list according to the selected option.
5. WHEN a user renames a project, THE Projects_Page SHALL update the project name and persist the change.
6. WHEN a user deletes a project, THE Projects_Page SHALL remove the project from the list and persist the removal.
7. WHEN a user duplicates a project, THE Projects_Page SHALL create a copy of the project in the list.
8. THE Projects_Page SHALL display a recent projects section.
9. IF the project list is empty, THEN THE Projects_Page SHALL display an empty state with guidance to create a transcription.

### Requirement 11: History Page

**User Story:** As a user, I want to review my past transcription jobs, so that I can find and reopen previous results.

#### Acceptance Criteria

1. THE History_Page SHALL display each previous job showing the file name, processing date, status, duration, language, and exported formats.
2. THE History_Page SHALL provide search, filter, and sort controls for the history list.
3. WHEN a user enters a search term, THE History_Page SHALL display only jobs matching the search term.
4. WHEN a user selects a history job, THE Transcription_App SHALL load and display the corresponding transcription result.
5. WHEN a user activates the clear history control, THE History_Page SHALL remove all entries from the Transcription_History.
6. IF the Transcription_History is empty, THEN THE History_Page SHALL display an empty state indicating no transcriptions exist.

### Requirement 12: About Page

**User Story:** As a visitor, I want an about page, so that I understand the purpose, capabilities, and technology behind the application.

#### Acceptance Criteria

1. THE About_Page SHALL display an overview describing the application as a demonstration and testing interface for an AI transcription backend.
2. THE About_Page SHALL display a summary of the application's features.
3. THE About_Page SHALL display the technology stack used by the application.
4. THE About_Page SHALL display a section describing planned future improvements.

### Requirement 13: Error Pages

**User Story:** As a user, I want clear error pages, so that I understand what went wrong and how to recover.

#### Acceptance Criteria

1. WHEN a route matches no defined page, THE Error_Page SHALL display a 404 Not Found explanation and an action control that returns the user to the application.
2. WHEN a transcription processing failure occurs, THE Error_Page SHALL display a Processing Error explanation and an action control that returns the user to the Transcribe_Page.
3. WHEN an unsupported file format is provided, THE Error_Page SHALL display an Unsupported File Format explanation, identify the accepted formats, and provide an action control that returns the user to the Transcribe_Page.

### Requirement 14: Design System and Component Library

**User Story:** As a developer maintaining the application, I want a reusable component library aligned to Material Design 3, so that the UI is consistent, modular, and maintainable.

#### Acceptance Criteria

1. THE Component_Library SHALL provide reusable components for cards, buttons, forms, tabs, tables, tooltips, toast notifications, progress indicators, skeleton loaders, and empty states.
2. THE Design_System SHALL apply Material Design 3 color roles using `--md-sys-color-*` tokens rather than hardcoded color values.
3. THE Design_System SHALL apply the Material Design 3 typography scale using `--md-sys-typescale-*` roles.
4. THE Design_System SHALL apply Material Design 3 shape tokens using `--md-sys-shape-corner-*` values rather than literal border-radius values.
5. THE Design_System SHALL convey elevation through Material Design 3 tonal surface colors.
6. THE Transcription_App SHALL constrain primary content width to a maximum of approximately 1040 pixels on wide viewports.

### Requirement 15: Theming (Light and Dark Mode)

**User Story:** As a user, I want light and dark modes with accessible contrast, so that I can use the application comfortably in different environments.

#### Acceptance Criteria

1. THE Theme_Controller SHALL provide a light mode and a dark mode.
2. WHEN a user selects a theme, THE Theme_Controller SHALL apply the selected theme across all pages.
3. WHEN a user selects a theme, THE Theme_Controller SHALL persist the selection so that it is applied on subsequent visits.
4. THE Design_System SHALL use one primary accent color and neutral background surfaces.
5. THE Design_System SHALL meet a text contrast ratio of at least 4.5 to 1 for normal-size text in both light mode and dark mode.

### Requirement 16: Animation and Motion

**User Story:** As a user, I want subtle, intentional animations, so that the interface feels polished and responsive without being distracting.

#### Acceptance Criteria

1. THE Animation_Layer SHALL integrate a lightweight animation library compatible with the vanilla-JavaScript and Vite stack.
2. THE Animation_Layer SHALL animate the upload interaction, processing progress, page transitions, hover effects, loading skeletons, and the transcript generation indicator.
3. THE Animation_Layer SHALL apply Material Design 3 motion easing and duration tokens for transitions.
4. IF the user's system indicates a reduced-motion preference, THEN THE Animation_Layer SHALL disable or minimize non-essential animations.

### Requirement 17: Accessibility

**User Story:** As a user relying on assistive technology or keyboard navigation, I want an accessible interface, so that I can use all features of the application.

#### Acceptance Criteria

1. THE Transcription_App SHALL allow all interactive controls to be operated using the keyboard.
2. WHEN an interactive element receives keyboard focus, THE Transcription_App SHALL display a visible focus indicator.
3. THE Transcription_App SHALL provide ARIA labels for interactive controls that lack visible text labels.
4. THE Transcription_App SHALL use semantic HTML elements for structural and interactive content.
5. THE Transcription_App SHALL provide responsive layouts that adapt across mobile, tablet, and desktop breakpoints.
6. THE Transcription_App SHALL conform to WCAG 2.2 Level AA success criteria where practical for the implemented components.

### Requirement 18: Performance

**User Story:** As a user, I want the application to load and respond quickly, so that testing transcriptions feels fast even with large content.

#### Acceptance Criteria

1. THE Transcription_App SHALL lazy-load page modules so that a page's code is loaded when the page is first navigated to.
2. THE Transcription_App SHALL apply code splitting so that page modules are delivered as separate bundles.
3. WHILE displaying a long transcript, THE Transcript_Viewer SHALL virtualize rendering to limit the number of mounted DOM segments.
4. THE Transcription_App SHALL defer loading of non-critical assets until they are required.

### Requirement 19: Scope Boundaries (Non-Goals)

**User Story:** As a stakeholder, I want the redesign to stay focused on transcription testing, so that the application remains lightweight and does not accumulate production-SaaS surfaces.

#### Acceptance Criteria

1. THE Transcription_App SHALL NOT include an authentication or login user interface.
2. THE Transcription_App SHALL NOT include user account management, billing, pricing, or subscription surfaces.
3. THE Transcription_App SHALL NOT include an API portal or API management surface.
4. THE Transcription_App SHALL NOT include live or streaming transcription surfaces.
