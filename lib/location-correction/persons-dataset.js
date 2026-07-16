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
// ---------------------------------------------------------------------------

const MINISTERS = [
  // === Current Government (Mahama Administration, 2025–) ===
  // Source: CIA World Leaders, updated Dec 2025
  { canonical: 'Samuel Nartey George', role: 'Min. of Communications (2025–)', aliases: ['Sam George', 'Nartey George', 'Sam Nartey George'] },
  { canonical: 'Cassiel Ato Forson', role: 'Min. of Finance / Acting Defence (2025–)', aliases: ['Ato Forson', 'Cassiel Ato', 'Dr Ato Forson', 'Cassiel Ato Baah Forson'] },
  { canonical: 'Haruna Iddrisu', role: 'Min. of Education (2025–)', aliases: ['Haruna', 'Haruna Iddrissu', 'Haruna Idrissu'] },
  { canonical: 'John Abdulai Jinapor', role: 'Min. of Energy (2025–)', aliases: ['Jinapor', 'John Jinapor', 'Abdulai Jinapor'] },
  { canonical: 'Ibrahim Murtala Mohammed', role: 'Min. of Environment (2025–)', aliases: ['Murtala Mohammed', 'Ibrahim Murtala'] },
  { canonical: 'Emelia Arthur', role: 'Min. of Fisheries (2025–)', aliases: ['Emelia Arthur'] },
  { canonical: 'Eric Opoku', role: 'Min. of Food & Agriculture (2025–)', aliases: ['Eric Opoku'] },
  { canonical: 'Samuel Okudzeto Ablakwa', role: 'Min. of Foreign Affairs (2025–)', aliases: ['Ablakwa', 'Okudzeto Ablakwa', 'Samuel Ablakwa'] },
  { canonical: 'Agnes Naa Momo Lartey', role: 'Min. of Gender (2025–)', aliases: ['Naa Momo Lartey', 'Agnes Lartey', 'Dr Lartey'] },
  { canonical: 'Kwabena Minta Akandoh', role: 'Min. of Health (2025–)', aliases: ['Akandoh', 'Minta Akandoh', 'Kwabena Akandoh'] },
  { canonical: 'Mohammed Mubarak Muntaka', role: 'Min. of Interior / Nat. Security (2025–)', aliases: ['Muntaka', 'Mubarak Muntaka', 'Alhaji Muntaka'] },
  { canonical: 'Dominic Ayine', role: 'Attorney General (2025–)', aliases: ['Dr Ayine', 'Dominic Akuritinga Ayine'] },
  { canonical: 'Abdul-Rashid Pelpuo', role: 'Min. of Labor (2025–)', aliases: ['Pelpuo', 'Dr Pelpuo', 'Abdul-Rashid Hassan Pelpuo'] },
  { canonical: 'Emmanuel Armah-Kofi Buah', role: 'Min. of Lands (2025–)', aliases: ['Armah-Kofi Buah', 'Emmanuel Buah'] },
  { canonical: 'Ahmed Ibrahim', role: 'Min. of Local Government (2025–)', aliases: ['Ahmed Ibrahim'] },
  { canonical: 'Governs Kwame Agbodza', role: 'Min. of Roads (2025–)', aliases: ['Agbodza', 'Governs Agbodza', 'Kwame Agbodza'] },
  { canonical: 'Abla Dzifa Gomashie', role: 'Min. of Tourism (2025–)', aliases: ['Dzifa Gomashie', 'Abla Gomashie'] },
  { canonical: 'Elizabeth Ofosu Agyare', role: 'Min. of Trade (2025–)', aliases: ['Ofosu Agyare', 'Elizabeth Agyare'] },
  { canonical: 'Joseph Bukari Nikpe', role: 'Min. of Transportation (2025–)', aliases: ['Nikpe', 'Bukari Nikpe'] },
  { canonical: 'Kenneth Gilbert Adjei', role: 'Min. of Works & Housing (2025–)', aliases: ['Kenneth Adjei', 'Gilbert Adjei'] },
  { canonical: 'George Opare Addo', role: 'Min. of Youth (2025–)', aliases: ['Opare Addo', 'Pablo'] },
  { canonical: 'Johnson Asiama', role: 'Governor, Bank of Ghana (2025–)', aliases: ['Dr Asiama', 'Johnson Asiama'] },
  { canonical: 'Victor Emmanuel Smith', role: 'Ambassador to the US (2025–)', aliases: ['Victor Smith'] },
  { canonical: 'Harold Adlai Agyeman', role: 'Permanent Rep. to UN (2025–)', aliases: ['Adlai Agyeman'] },

  // === Previous Government (Akufo-Addo Administration, 2017–2025) ===
  // Finance
  { canonical: 'Ken Ofori-Atta', role: 'Minister for Finance (2017–2023)', aliases: ['Ofori-Atta', 'Ken Ofori Atta'] },
  { canonical: 'Seth Terkper', role: 'Minister for Finance (2013–2017)', aliases: ['Terkper', 'Seth Terkpeh'] },
  { canonical: 'Mohammed Amin Adam', role: 'Minister for Finance (2024–2025)', aliases: ['Amin Adam', 'Dr Amin Adam'] },
  // Attorney General
  { canonical: 'Godfred Yeboah Dame', role: 'Attorney General (2021–2025)', aliases: ['Godfred Dame', 'Dame'] },
  { canonical: 'Gloria Akuffo', role: 'Attorney General (2017–2021)', aliases: ['Gloria Akuffo'] },
  { canonical: 'Marietta Brew Appiah-Oppong', role: 'Attorney General (2013–2017)', aliases: ['Marietta Brew', 'Appiah-Oppong'] },
  // Interior
  { canonical: 'Ambrose Dery', role: 'Minister for Interior (2017–2025)', aliases: ['Dery', 'Ambrose Derry'] },
  // Education
  { canonical: 'Matthew Opoku Prempeh', role: 'Minister for Education (2017–2021)', aliases: ['NAPO', 'Opoku Prempeh', 'Dr Prempeh'] },
  // Health
  { canonical: 'Kwaku Agyeman-Manu', role: 'Minister for Health (2017–2023)', aliases: ['Agyeman-Manu', 'Agyemang Manu'] },
  // Defence
  { canonical: 'Dominic Nitiwul', role: 'Minister for Defence (2017–2025)', aliases: ['Nitiwul', 'Dominic Nitiwol'] },
  // Trade
  { canonical: 'Alan Kyerematen', role: 'Minister for Trade (2017–2023)', aliases: ['Kyerematen', 'Alan Cash', 'Alan Kyeremanteng'] },
  // Roads
  { canonical: 'Kwasi Amoako-Atta', role: 'Minister for Roads (2017–2025)', aliases: ['Amoako-Atta', 'Kwasi Amoako Atta', 'Amoako Attah'] },
  // Energy
  { canonical: 'Boakye Agyarko', role: 'Minister for Energy (2017–2018)', aliases: ['Agyarko'] },
  // Lands
  { canonical: 'Samuel Abu Jinapor', role: 'Minister for Lands (2021–2025)', aliases: ['Abu Jinapor', 'Samuel Jinapor'] },
  // Information
  { canonical: 'Kojo Oppong Nkrumah', role: 'Minister for Information (2018–2025)', aliases: ['Oppong Nkrumah', 'Kojo Oppong'] },
  // Local Government
  { canonical: 'Dan Botwe', role: 'Minister for Local Government (2021–2025)', aliases: ['Botwe', 'Dan Kwaku Botwe'] },
  // Food & Agriculture
  { canonical: 'Owusu Afriyie Akoto', role: 'Minister for Food & Agriculture (2017–2023)', aliases: ['Afriyie Akoto', 'Dr Akoto'] },
  // Communications
  { canonical: 'Ursula Owusu-Ekuful', role: 'Minister for Communications (2017–2025)', aliases: ['Ursula Owusu', 'Owusu-Ekuful'] },
  // Employment
  { canonical: 'Ignatius Baffuor Awuah', role: 'Minister for Employment (2017–2025)', aliases: ['Baffuor Awuah', 'Ignatius Baffour Awuah'] },
  // Works & Housing
  { canonical: 'Francis Asenso-Boakye', role: 'Minister for Works & Housing (2021–2025)', aliases: ['Asenso-Boakye', 'Francis Asenso Boakye'] },
  // Majority/Minority leaders (previous parliament)
  { canonical: 'Osei Kyei-Mensah-Bonsu', role: 'Majority Leader (2017–2025)', aliases: ['Kyei-Mensah-Bonsu', 'Osei Kyei Mensah Bonsu'] },
  // Chief Justices
  { canonical: 'Kwasi Anin-Yeboah', role: 'Chief Justice (2020–2023)', aliases: ['Anin-Yeboah', 'Justice Anin Yeboah'] },
  { canonical: 'Gertrude Sackey Torkornoo', role: 'Chief Justice (2023–)', aliases: ['Torkornoo', 'Justice Torkornoo', 'Gertrude Torkornoo'] },
  // Former MPs (notable)
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
  ...CULTURAL_FIGURES,
];

module.exports = {
  PRESIDENTS,
  VICE_PRESIDENTS,
  SPEAKERS,
  MINISTERS,
  CULTURAL_FIGURES,
  ALL_PERSONS,
};
