# Parliament Hansard Platform — Development Task Log

A chronological record of the work completed over a two-week development period (10 working days) on the Parliament Hansard transcription platform. The entries walk through the project from its very first day — gathering requirements and researching transcription services — all the way through building the platform, testing it, and getting it ready to hand over. Each task builds naturally on the one before it, telling the story of how the platform came together.

---

## Task 1 — Project Kickoff, Requirements Gathering & Early Research into Transcription Services

**Task Description:**
Was tasked to start on a new project for the Parliament Hansard Platform where I would help turn recorded parliamentary sessions into written, searchable records — the kind of official record that Hansard editors work with every day. The first part of the work was really about listening and learning. I sat down to understand what the platform actually needed to do and who would be using it. Parliament's proceedings are a mix of languages: a lot of the debate happens in English, but members very often switch into local Ghanaian languages such as Twi, Ewe, Ga, and Dagbani, especially when they are speaking about issues close to their constituencies. That single fact shaped almost every decision that followed, because it meant an ordinary English-only transcription tool would never be enough on its own.

I spent this first stretch of the project writing down the core needs in plain terms: the platform had to produce accurate written transcripts from audio recordings, it had to cope with both English and Ghanaian languages, it needed to show who was speaking and when, editors had to be able to correct the text by hand, and finished transcripts had to be downloadable in a few common formats. On top of that, users needed a way to look back at everything they had transcribed before. With those needs written down, I began looking around at what transcription services already existed in the market, paying special attention to any that could handle Ghanaian languages, since that was clearly going to be the hardest part to get right. This early scan pointed me toward a small group of services worth studying more closely.

**Expected Outcome / Deliverable:**
A clear, written summary of what the Hansard platform needs to do, along with a shortlist of transcription services worth investigating further — with the ability to handle Ghanaian languages flagged as the single most important thing to get right.

**Status:** Completed

---

## Task 2 — Researching Khaya AI (GhanaNLP) and Its Support for Ghanaian Languages

**Task Description:**
This task was dedicated entirely to getting to know Khaya AI, a transcription service built by GhanaNLP, a research group focused on Ghanaian and West African languages. Because the platform absolutely had to handle the local languages spoken in Parliament, Khaya AI was the most important service to understand properly, and I wanted to be confident it could actually do the job before committing to it.

I spent time reading through Khaya AI's documentation and getting familiar with how it works and what it offers. The most reassuring finding was that Khaya AI is purpose-built for exactly the languages we care about — it supports Asante Twi, Ewe, Ga, Dagbani, Frafra, and a growing list of others, which lines up very well with the languages members of Parliament actually use. It offers two flavours of its transcription service: a faster one aimed at short, single sentences, and a more thorough one designed for longer recordings. For parliamentary audio, which tends to run long, the more thorough option was clearly the right fit. I also looked into the practical side of using it: how you send audio to it, how it identifies which language to transcribe, how it hands back the finished text, and what happens when something goes wrong or when a monthly usage limit is reached.

Alongside the strengths, I made a point of noting the limitations honestly, so there would be no surprises later. Khaya AI works with uploaded audio files rather than links to audio, it does not label who is speaking, and it does not offer live or streaming transcription. None of these were deal-breakers for our use case, but they did confirm that Khaya AI would be best paired with a second service to cover English proceedings and speaker identification. Overall, the research left me confident that Khaya AI was a solid, credible foundation for the Ghanaian-language side of the platform.

**Expected Outcome / Deliverable:**
A thorough, well-rounded understanding of Khaya AI — what languages it covers, how it is used in practice, where it shines, and where its limits lie — giving the team confidence that it was the right choice for transcribing Ghanaian-language parliamentary audio.

**Status:** Completed

---

## Task 3 — Evaluating Deepgram and Deciding on a Two-Service Approach

**Task Description:**
Having satisfied myself that Khaya AI would handle the Ghanaian-language side well, I turned to finding a strong partner service to handle the English side of the platform, and to make sure the two could work together comfortably. The service I focused on was Deepgram, a well-established transcription provider with a strong reputation for English accuracy.

I looked at what Deepgram brought to the table and found it complemented Khaya AI almost perfectly. Where Khaya AI is specialised for Ghanaian languages, Deepgram is excellent at English and, importantly, it can do the things Khaya AI cannot — most notably telling apart different speakers, adding punctuation, and marking exactly when each word was spoken. These are all things that matter a great deal for a Hansard record, where knowing who said what, and when, is essential. Deepgram is also flexible about how audio is provided, accepting both uploaded files and links to audio, which gave us more options.

With both services understood, I made the key architectural decision for the whole project: rather than choosing one service over the other, the platform would use both, letting the user pick whichever one suits the recording in front of them. Deepgram would be the default for English-heavy proceedings, and Khaya AI would be there for segments in local languages. To keep things simple for everyone using the platform, I decided that no matter which service did the work, the results would be presented in the same consistent way, so the experience of reading and editing a transcript would feel identical either way. I also settled the broader technology choices for the project at this point, picking tools that were dependable, well-suited to the team's way of working, and easy to maintain over time.

**Expected Outcome / Deliverable:**
A confirmed plan to use two transcription services side by side — Deepgram for English and Khaya AI for Ghanaian languages — with a shared, consistent way of presenting results, and an agreed set of technology choices for building the rest of the platform.

**Status:** Completed

---

## Task 4 — Building the Foundation That Connects Both Transcription Services

**Task Description:**
With the research done and the plan settled, this task was about actually building the behind-the-scenes engine of the platform — the part that receives audio, sends it off to the right transcription service, and hands back the finished text. This is the piece that makes everything else possible, so getting it solid and reliable was the priority.

I built the central server that all the platform's requests flow through. It handles receiving uploaded audio, keeps things secure by issuing and checking short-lived access passes so that only legitimate users can request transcriptions, and it knows how to talk to both transcription services. A good deal of care went into the Khaya AI side in particular. I built a dedicated piece of the system whose only job is to communicate with Khaya AI: it sends the audio along with the chosen Ghanaian language, waits for the response, and then tidies that response up into the same neat, predictable shape the rest of the platform expects. Because Khaya AI can return its results in a few slightly different ways depending on the situation, I made sure this piece could gracefully handle each of them without tripping up.

I also made the platform honest about problems. If Khaya AI can't be reached, if the access key is missing or wrong, or if a monthly usage limit has been hit, the platform now responds with a clear, understandable message rather than a confusing failure. On top of that, I added a simple way for the platform to ask Khaya AI which languages it currently supports, so the list shown to users always reflects what is genuinely available rather than a hard-coded guess. By the end of this task, the foundation could take an audio file and produce a transcript through either service, with sensible handling for the things that can go wrong.

**Expected Outcome / Deliverable:**
A working foundation for the platform that can accept audio, keep requests secure, and produce transcripts through either Deepgram or Khaya AI — with a dedicated, well-behaved connection to Khaya AI and clear, friendly handling of errors and usage limits.

**Status:** Completed

---

## Task 5 — Testing and Strengthening the Khaya AI Transcription Experience

**Task Description:**
Before moving on to the parts of the platform that people actually see and touch, I wanted to be completely sure that the Ghanaian-language transcription worked properly and behaved well under real conditions. This task was all about putting the Khaya AI side through its paces and smoothing out any rough edges.

I ran real Ghanaian-language audio through the platform across several of the supported languages, including Twi, Ewe, Ga, and Dagbani, to confirm that the transcripts coming back were genuinely usable for the kind of speech heard in Parliament. I paid attention to how the platform coped with the different ways Khaya AI can phrase its responses, making sure that in every case the transcript ended up looking the same and clean to the rest of the system. Just as importantly, I deliberately triggered the things that can go wrong — a missing or incorrect access key, a usage limit being reached, the service being temporarily unavailable — and checked that each situation produced a clear, calm message instead of an alarming breakdown.

I also confirmed some practical details that matter for day-to-day reliability. The list of supported languages could be fetched successfully so the platform always shows an up-to-date choice to users, and languages were handled correctly even when their names or codes contained unusual characters. Finally, I made sure that if Khaya AI ever became unavailable, the platform as a whole would keep working for English transcription through Deepgram rather than falling over entirely. Coming out of this task, I had real confidence that the Ghanaian-language transcription — the heart of what makes this platform special — was dependable and ready to build on.

**Expected Outcome / Deliverable:**
A thoroughly tested and hardened Ghanaian-language transcription experience, with confirmed transcription quality across several local languages, calm and clear handling of every error situation, and assurance that the platform stays useful even if Khaya AI is temporarily unavailable.

**Status:** Completed

---

## Task 6 — Rebuilding the Platform's Interface as a Modern, Multi-Page Experience

**Task Description:**
With a dependable foundation in place, attention shifted to the part of the platform that people would actually use every day. The original interface was a single cramped page, and this task was about reimagining it as a clean, modern application made up of several distinct pages that are easy to move between.

I rebuilt the front of the platform from the ground up as a proper multi-page experience, with separate, purpose-built areas: a welcoming landing page, a workspace for doing transcriptions, a page for organising work into projects, a page for browsing past transcriptions, and an about page. I set up smooth navigation so moving between these areas feels instant and natural, without the whole page reloading each time, and so that sharing or bookmarking a link takes you straight back to the right place. Care was taken so that pages only load when they are actually needed, keeping the platform quick and responsive.

I also put in place the underlying pieces that hold the experience together behind the scenes: the platform now remembers a user's preference for light or dark appearance, keeps their session running smoothly and securely, and quietly saves a history of their transcriptions on their own device so they can return to earlier work. Crucially, all of this was done while carefully preserving everything the platform could already do, so nothing that worked before was lost in the rebuild — the transcription features, the two-service setup, and the saved history all carried over intact. By the end of this task, the skeleton of a much nicer, more capable interface was standing and ready to be filled in.

**Expected Outcome / Deliverable:**
A freshly rebuilt, modern multi-page interface with smooth navigation, remembered user preferences, secure ongoing sessions, and a preserved history of past work — all while keeping every existing capability intact.

**Status:** Completed

---

## Task 7 — Creating a Consistent Look and a Library of Reusable Building Blocks

**Task Description:**
A platform used for serious, official work needs to feel polished, consistent, and trustworthy. This task focused on giving the whole application a coherent visual identity and building a set of reusable pieces so that every part of the platform looks and behaves the same way.

I established a single, unified design language covering colours, text sizes, spacing, rounded corners, and gentle motion, all drawn from a well-regarded modern design approach. This gave the platform one consistent personality rather than a patchwork of different styles. I made sure it worked beautifully in both a light and a dark appearance, and I paid close attention to making text comfortably readable in both, with enough contrast for people who might otherwise struggle to read it.

On top of that foundation, I built a library of common, reusable building blocks — things like buttons, cards, form fields, tabs, tables, tooltips, notification pop-ups, progress indicators, loading placeholders, and friendly "nothing here yet" messages — so that assembling the actual pages later would be faster and the result would feel uniform throughout. I also built the shared framework that appears on every page, including the top navigation bar that highlights where you currently are and collapses neatly into a menu on smaller screens, the footer, and the control for switching between light and dark appearance. Accessibility was baked into these pieces from the start, so they can be used comfortably with a keyboard and by people relying on assistive technology.

**Expected Outcome / Deliverable:**
A consistent, professional visual identity that works in both light and dark modes, together with a reusable library of interface building blocks and a shared page framework — giving the platform a polished, trustworthy feel and making the rest of the interface faster to assemble.

**Status:** Completed

---

## Task 8 — Building the Transcription Workspace Where the Real Work Happens

**Task Description:**
This task brought together everything built so far into the platform's centrepiece: the workspace where a user actually uploads a recording, chooses how it should be transcribed, and works with the finished text. This is the page people would spend most of their time in, so it needed to be both capable and pleasant to use.

I built a comfortable way to add audio, letting users either drag a file in or pick one the usual way, with clear feedback showing the file's name and size, a preview so they can listen back, and a progress indicator while it uploads. I then built the controls that let users shape how the transcription is done — most importantly, the choice between the two transcription services. When a user chooses the Ghanaian-language service, the platform presents a tidy dropdown of the available local languages, freshly pulled from the service so it is always current; when they choose the English service, the relevant options for that appear instead. The platform gently guides users by only offering the options that make sense for whichever service they have picked.

Once a transcription is running, the workspace keeps the user informed with a clear sense of progress rather than leaving them staring at a blank screen. When the text comes back, I gave them a rich viewer to work with it: they can read it with speaker labels and timestamps where available, click a timestamp to jump the audio to that exact moment, search through the text, make corrections by hand, copy it, and download it. I also added a summary panel alongside the transcript showing helpful details at a glance, and the ability to export the finished record in several common document formats so it can be used elsewhere. Finished transcriptions are automatically saved to the user's history so nothing is ever lost.

**Expected Outcome / Deliverable:**
A complete, friendly transcription workspace where users can add audio, choose between the English and Ghanaian-language services, follow the progress of their transcription, and then read, correct, search, and export the finished text — the true heart of the platform.

**Status:** Completed

---

## Task 9 — Finishing the Remaining Pages and Making the Platform Welcoming to Everyone

**Task Description:**
With the core workspace in place, this task completed the rest of the platform's pages and made sure the whole experience was accessible and inclusive for every user, however they choose to interact with it.

I built out the remaining areas of the platform. The landing page gives first-time visitors a warm, clear introduction to what the platform does, with a headline, an at-a-glance walkthrough of how it works, a set of highlighted features, and a taste of what a finished transcript looks like. The projects page lets users organise their transcriptions into tidy groups they can search, sort, rename, duplicate, or remove. The history page lets users look back over everything they have transcribed, search and filter through it, and reopen any past result with a single click — and it clearly marks which service produced each one, so English and Ghanaian-language transcriptions are easy to tell apart. The about page explains the platform's purpose and the thinking behind it, and I added friendly, helpful error pages for the moments when something goes wrong or a page can't be found.

Beyond finishing the pages, I put real effort into making the platform usable by everyone. I made sure every control can be operated with a keyboard alone, that it is clear where you are on the page as you move through it, that buttons and icons carry proper descriptions for people using assistive technology, and that the layout adapts gracefully whether someone is on a phone, a tablet, or a desktop. I also made the platform's gentle animations respectful of people who prefer reduced motion, quietening them down automatically for anyone who has asked their device for a calmer experience.

**Expected Outcome / Deliverable:**
A complete set of finished, polished pages — landing, projects, history, about, and error pages — with clear separation between English and Ghanaian-language transcriptions in the history, and a platform that is genuinely welcoming and usable for everyone, on any device.

**Status:** Completed

---

## Task 10 — Testing, Final Checks and Getting the Platform Ready to Hand Over

**Task Description:**
The final stretch of the two weeks was about making sure everything was trustworthy, reliable, and ready to be handed over with confidence. Good work deserves to be dependable, so this task was about proving that the platform behaves correctly and preparing it for the outside world.

I set up an automated testing safety net focused on the transcription engine, and especially on the Khaya AI connection, so that any future change that accidentally broke something would be caught quickly rather than slipping through unnoticed. These tests carefully check that audio requests are put together correctly, that the transcripts coming back are tidied into the expected shape no matter which form they arrive in, and that every kind of error — a missing key, a usage limit, an unreachable service — is handled exactly as intended. Just as importantly, the tests were designed to run entirely on their own without ever contacting the real transcription services, so they are fast, repeatable, and cost nothing to run.

Alongside the testing, I put the platform through its final checks. I confirmed that it builds cleanly and loads quickly, and I walked through both transcription services from start to finish to make sure the whole journey — from uploading audio to reading and exporting the finished transcript — worked smoothly for English and Ghanaian-language recordings alike. Finally, I tidied up and updated the platform's documentation so that anyone picking up the project later would understand how it fits together and how to run it, and I confirmed that the setup needed to deploy the platform was in place and ready. Coming out of this task, the platform was tested, verified, documented, and prepared for the next stage.

**Expected Outcome / Deliverable:**
A dependable, well-tested platform with an automated safety net around the transcription engine, confirmed to build and run smoothly, with both transcription services verified end to end, up-to-date documentation, and the groundwork laid for deployment.

**Status:** Completed
