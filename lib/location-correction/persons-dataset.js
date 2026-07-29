/**
 * Ghana Persons Dataset — Presidents, Vice Presidents, Speakers, and
 * notable Ministers frequently referenced in parliamentary transcripts.
 *
 * Used by the post-processing correction engine to fix ASR misspellings
 * of proper names. Each entry has a canonical form and optional aliases
 * (common ASR misrenderings or alternate spellings).
 *
 * Format: { canonical: string, role: string, aliases?: string[] }
 */

'use strict';

// ---------------------------------------------------------------------------
// Presidents of Ghana (1st–5th Republic + Transitional)
// ---------------------------------------------------------------------------

const PRESIDENTS = [
  // First Republic
  { canonical: 'Kwame Nkrumah', role: 'President (1960–1966)', aliases: [
    'Nkrumah', 'Kwame Nkruma', 'Kwami Nkrumah', 'Kwame Enkrumah', 'Kwame Nkrouma',
    'Kwameh Nkrumah', 'Quame Nkrumah', 'Kwame Nkroomah', 'Kwame Inkrumah',
    'Kwame Nkrumma', 'Kwami Nkruma', 'Kwame Nkruhmah', 'Kwame Enkrooma',
    'Kwame Nkruma', 'K. Nkrumah', 'Kwame N\'krumah', 'Kwame Nkrumahr',
    'Kwameh Nkroumah', 'Quameh Nkrumah', 'Kwame Nkroomah', 'Nkrouma',
  ] },
  // National Liberation Council
  { canonical: 'Joseph Arthur Ankrah', role: 'Head of State (1966–1969)', aliases: [
    'Ankrah', 'General Ankrah', 'J.A. Ankrah', 'Ankra', 'Joseph Ankrah',
    'Gen Ankrah', 'Ankrah Joseph', 'Ankra Joseph', 'Joe Ankrah',
    'Joseph Ankra', 'Ankraa', 'Ankwah', 'General Ankra',
    'J. A. Ankrah', 'Joseph A. Ankrah', 'General Joseph Ankrah',
    'Lt Gen Ankrah', 'Lt. Gen. Ankrah', 'Ankrah General', 'Anquah',
  ] },
  { canonical: 'Akwasi Afrifa', role: 'Head of State (1969)', aliases: [
    'Afrifa', 'General Afrifa', 'Akwasi Amankwa Afrifa', 'Akwasi Afreefa',
    'Kwasi Afrifa', 'Gen Afrifa', 'Afrifa Akwasi', 'Afreefa',
    'A.A. Afrifa', 'Akwasi Afrefa', 'Lt Gen Afrifa', 'Afrifah',
    'Akwasi Afrifah', 'General Akwasi Afrifa', 'Akwesi Afrifa',
    'Kwasi Afreefa', 'Afrifa General', 'Afrifa Amankwa', 'Afrifer', 'Afriffa',
  ] },
  // Second Republic
  { canonical: 'Edward Akufo-Addo', role: 'President (1970–1972)', aliases: [
    'Akufo-Addo', 'Edward Akufo Addo', 'Edward Akuffo Addo', 'E. Akufo-Addo',
    'Edward Akufoaddo', 'Akufo Addo Edward', 'Edward Akufo-Ado',
    'Akuffo-Addo', 'Edward Akuffo-Addo', 'Akufo Ado', 'E. Akuffo-Addo',
    'Akufoaddo Edward', 'Edward Akufoadoh', 'Akufo Addo', 'Akuffo Addo',
    'Edward Akufoaddo', 'Akufado', 'Akufaddo', 'Edward Akufado', 'Akufoadoh',
  ] },
  { canonical: 'Kofi Abrefa Busia', role: 'Prime Minister (1969–1972)', aliases: [
    'Busia', 'Dr Busia', 'K.A. Busia', 'Busiah', 'Boosiah', 'Boosia',
    'Kofi Busia', 'Dr. Busia', 'Professor Busia', 'PM Busia',
    'K. A. Busia', 'Kofi A. Busia', 'Busia Kofi', 'Dr Kofi Busia',
    'Bussia', 'Bussiah', 'Bousia', 'Busiaa', 'Dr Busiah', 'Busiyah',
  ] },
  // Supreme Military Council
  { canonical: 'Ignatius Kutu Acheampong', role: 'Head of State (1972–1978)', aliases: [
    'Acheampong', 'General Acheampong', 'I.K. Acheampong', 'Kutu Acheampong',
    'Acheampung', 'Achampong', 'Achiampong', 'Gen Acheampong',
    'Ignatius Acheampong', 'Col Acheampong', 'Achempong', 'Acheampng',
    'I. K. Acheampong', 'Kutu Achampong', 'Acheampong Kutu',
    'Achiampung', 'Acheampong Ignatius', 'Gen Kutu Acheampong',
    'Achempung', 'General Kutu Acheampong',
  ] },
  { canonical: 'Fred Akuffo', role: 'Head of State (1978–1979)', aliases: [
    'General Akuffo', 'Fred W.K. Akuffo', 'Fred Akufo', 'Gen Akuffo',
    'Fred Akufo', 'Lt Gen Akuffo', 'Akuffo', 'Fred Akuffoh',
    'Akufo Fred', 'General Fred Akuffo', 'F.W.K. Akuffo', 'Akuffo Fred',
    'Frederick Akuffo', 'Fred Akufor', 'Fred Akufoh', 'Akuffoh',
    'Gen Fred Akuffo', 'Fred Akuffo General', 'Akuffo General', 'Fred Akufado',
  ] },
  // AFRC / Third Republic
  { canonical: 'Jerry John Rawlings', role: 'Head of State (1979, 1981–1993) / President (1993–2001)', aliases: [
    'Rawlings', 'J.J. Rawlings', 'JJ Rawlings', 'Flight Lieutenant Rawlings',
    'Flt Lt Rawlings', 'Jerry Rawlings', 'Rolling', 'Rollings',
    'JJ Rolling', 'JJ Rollings', 'Jay Jay Rawlings', 'Jay Jay Rolling',
    'Rawling', 'Rawlins', 'Rollins', 'JJ Rollins', 'Rawllings', 'Rolings',
    'JJ Rawlins', 'JJ Rawling',
  ] },
  { canonical: 'Hilla Limann', role: 'President (1979–1981)', aliases: [
    'Limann', 'Dr Limann', 'Dr Hilla Limann', 'Liman', 'Leeman', 'Lehman',
    'Hillah Limann', 'Hila Limann', 'Dr. Limann', 'Dr Liman', 'H. Limann',
    'Hilla Liman', 'Limon', 'Leaman', 'Limman', 'Hillah Liman',
    'Hilla Lehman', 'President Limann', 'Dr Hilla Liman', 'Hilar Limann',
  ] },
  // Fourth Republic
  { canonical: 'John Agyekum Kufuor', role: 'President (2001–2009)', aliases: [
    'Kufuor', 'J.A. Kufuor', 'President Kufuor', 'Kuffour', 'Kuffuor',
    'Kufour', 'Kufor', 'Koofuor', 'Kufuour', 'John Kufuor',
    'J. A. Kufuor', 'Kufuor John', 'Koofour', 'Kufoor',
    'Kufuor Agyekum', 'Agyekum Kufuor', 'Kufur', 'Kofuor',
    'John A. Kufuor', 'President Kuffour',
  ] },
  { canonical: 'John Evans Atta Mills', role: 'President (2009–2012)', aliases: [
    'Atta Mills', 'Mills', 'Professor Mills', 'Prof Mills', 'J.E.A. Mills',
    'John Atta Mills', 'Attah Mills', 'Ata Mills', 'John Mills',
    'Prof Atta Mills', 'Professor Atta Mills', 'Evans Atta Mills',
    'Atta-Mills', 'Atah Mills', 'Ata-Mills', 'President Mills',
    'John Evans Mills', 'John Attah Mills', 'Atta Millz', 'Attamills',
  ] },
  { canonical: 'John Dramani Mahama', role: 'President (2012–2017, 2025–)', aliases: [
    'Mahama', 'John Mahama', 'President Mahama', 'J.D. Mahama',
    'Dramani Mahama', 'Mahamah', 'Mohama', 'John Dramani',
    'Mahama John', 'Dramani', 'Mahamma', 'Muhama', 'Mahaama',
    'John Dramani Shama', 'John Dramani Mohama', 'John Dramani Mahamah',
    'Mahma', 'Mahamer', 'Mohamma', 'John D. Mahama',
  ] },
  { canonical: 'Nana Addo Dankwa Akufo-Addo', role: 'President (2017–2025)', aliases: [
    'Nana Akufo-Addo', 'Nana Addo', 'Akufo-Addo', 'President Akufo-Addo',
    'Nana Akuffo-Addo', 'Nana Akufo Addo', 'Akufoaddo', 'Akuffo Addo',
    'Nana Addo Dankwa', 'Nana Addo Akufo-Addo', 'Nana Akufoaddo',
    'Nana Addo Dankwa Akufoaddo', 'Addo Tankwa Akufo-Addo',
    'Addo Tarkwa Akufo-Addo', 'Addo Dangkwa Akufo-Addo',
    'Akufado', 'Akufaddo', 'Nana Addo Akufado', 'Nana Akufado',
    'Nana Addo Dankwa Akuffo-Addo',
  ] },
];

// ---------------------------------------------------------------------------
// Vice Presidents (Fourth Republic)
// ---------------------------------------------------------------------------

const VICE_PRESIDENTS = [
  { canonical: 'Kow Nkensen Arkaah', role: 'Vice President (1993–1997)', aliases: ['Arkaah', 'K.N. Arkaah'] },
  { canonical: 'John Evans Atta Mills', role: 'Vice President (1997–2001)', aliases: ['Atta Mills'] },
  { canonical: 'Aliu Mahama', role: 'Vice President (2001–2009)', aliases: ['Alhaji Aliu Mahama'] },
  { canonical: 'John Dramani Mahama', role: 'Vice President (2009–2012)', aliases: ['Mahama'] },
  { canonical: 'Kwesi Amissah-Arthur', role: 'Vice President (2012–2017)', aliases: ['Amissah-Arthur', 'Kwesi Amissah Arthur', 'Paa Kwesi Amissah-Arthur'] },
  { canonical: 'Mahamudu Bawumia', role: 'Vice President (2017–2025)', aliases: ['Bawumia', 'Dr Bawumia', 'Bawumiah'] },
  { canonical: 'Jane Naana Opoku-Agyemang', role: 'Vice President (2025–)', aliases: ['Naana Opoku-Agyemang', 'Prof Opoku-Agyemang', 'Jane Naana', 'Opoku-Agyemang'] },
];

// ---------------------------------------------------------------------------
// Speakers of Parliament (Fourth Republic)
// ---------------------------------------------------------------------------

const SPEAKERS = [
  { canonical: 'Justice D.F. Annan', role: 'Speaker (1993–2001)', aliases: ['Justice Annan', 'D.F. Annan'] },
  { canonical: 'Peter Ala Adjetey', role: 'Speaker (2001–2005)', aliases: ['Ala Adjetey', 'Adjetey'] },
  { canonical: 'Ebenezer Begyina Sekyi-Hughes', role: 'Speaker (2005–2009)', aliases: ['Sekyi-Hughes', 'Sekyi Hughes'] },
  { canonical: 'Joyce Bamford-Addo', role: 'Speaker (2009–2013)', aliases: ['Bamford-Addo', 'Madam Speaker Bamford-Addo'] },
  { canonical: 'Edward Doe Adjaho', role: 'Speaker (2013–2017)', aliases: ['Adjaho', 'Doe Adjaho'] },
  { canonical: 'Aaron Mike Oquaye', role: 'Speaker (2017–2021)', aliases: ['Mike Oquaye', 'Professor Oquaye', 'Oquaye'] },
  { canonical: 'Alban Sumana Kingsford Bagbin', role: 'Speaker (2021–)', aliases: ['Bagbin', 'Alban Bagbin', 'Speaker Bagbin', 'Rt Hon Bagbin'] },
];

// ---------------------------------------------------------------------------
// Notable Ministers (commonly referenced in parliamentary proceedings)
// Comprehensive dataset in ministers-dataset.js
// ---------------------------------------------------------------------------

const { ALL_MINISTERS: MINISTERS } = require('./ministers-dataset');

// ---------------------------------------------------------------------------
// Chief Justices & Notable Non-Ministerial Figures
// ---------------------------------------------------------------------------

const OTHER_NOTABLES = [
  { canonical: 'Kwasi Anin-Yeboah', role: 'Chief Justice (2020–2023)', aliases: ['Anin-Yeboah', 'Justice Anin Yeboah'] },
  { canonical: 'Gertrude Sackey Torkornoo', role: 'Chief Justice (2023–)', aliases: ['Torkornoo', 'Justice Torkornoo', 'Gertrude Torkornoo'] },
  { canonical: 'B.B. Carboo', role: 'Former MP for Ningo-Prampram (Stanley Basil Bade Carboo)', aliases: [
    'BB Carboo', 'BB Kabu', 'BB Kabo', 'BB Carbu', 'BB Kaboo',
    'Carboo',
    'Basil Bade Carboo', 'Basil Carboo', 'Bade Carboo',
    'bb kabo', 'bb kabu', 'bibi kabo', 'bibi kabu',
    'Kibi Kabo', 'Kibi Kabu', 'Kibi Carboo',
    'bb carboo', 'b.b. carboo', 'b.b. kabu', 'b.b. kabo',
    'Stanley Carboo', 'Stanley Basil Bade Carboo', 'Nene Carboo',
  ]},
];

// ---------------------------------------------------------------------------
// Cultural & Notable Figures
// ---------------------------------------------------------------------------

const CULTURAL_FIGURES = [
  { canonical: 'Kwaku Ananse', role: 'Folklore (Ananse the Spider)', aliases: [
    'Ananse', 'Anansi', 'Anancy', 'Kwaku Anansi', 'Kweku Ananse',
    'Kweku Anansi', 'Kokwanansi', 'Kokwananse', 'Kok Wanansi',
    'Ko Kwanansi', 'Ananse Spider', 'Kwaku Anancy', 'Kwaku Anansy',
    'Kwaku Anance', 'Ananse Kwaku', 'Kokoananse', 'Kwakwananse',
    'Kwaku Anansie', 'Ananase', 'Anansi Kwaku', 'Kwaku Anansee',
  ] },
];

// ---------------------------------------------------------------------------
// Combined dataset export
// ---------------------------------------------------------------------------

const ALL_PERSONS = [
  ...PRESIDENTS,
  ...VICE_PRESIDENTS,
  ...SPEAKERS,
  ...MINISTERS,
  ...OTHER_NOTABLES,
  ...CULTURAL_FIGURES,
];

module.exports = {
  PRESIDENTS,
  VICE_PRESIDENTS,
  SPEAKERS,
  MINISTERS,
  OTHER_NOTABLES,
  CULTURAL_FIGURES,
  ALL_PERSONS,
};
