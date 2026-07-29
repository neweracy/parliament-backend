/**
 * Ghana Ministers Dataset — Comprehensive list of ministers across all
 * republics and transitional governments (1957–2025).
 *
 * Organized by government/administration. Used by the post-processing
 * correction engine to fix ASR misspellings of ministerial names.
 *
 * Format: { canonical: string, role: string, aliases?: string[] }
 * Each entry has at least 10 aliases covering: surname, initials,
 * common ASR misspellings, phonetic variants, and shortened forms.
 */

'use strict';

// ---------------------------------------------------------------------------
// First Republic — Nkrumah Government (1957–1966)
// ---------------------------------------------------------------------------

const FIRST_REPUBLIC_MINISTERS = [
  { canonical: 'Komla Agbeli Gbedemah', role: 'Min. of Finance (1957–1961)', aliases: [
    'Gbedemah', 'K.A. Gbedemah', 'Komla Gbedemah', 'Agbeli Gbedemah',
    'Gbedemeh', 'Gbedema', 'K. Gbedemah', 'Komla Agbeli',
    'Gbedemah Komla', 'K.A. Gbedema', 'Gbedehmah', 'Gbedemahr',
  ] },
  { canonical: 'Kojo Botsio', role: 'Min. of Foreign Affairs (1957–1963)', aliases: [
    'Botsio', 'K. Botsio', 'Kojo Botsio', 'Botso', 'Bostio',
    'Kojo Botso', 'K. Botso', 'Botsyo', 'Kodjoe Botsio',
    'Botchio', 'Bochio', 'Kojo Bostio',
  ] },
  { canonical: 'Krobo Edusei', role: 'Min. of Transport/Interior', aliases: [
    'Edusei', 'Krobo Edusie', 'K. Edusei', 'Krobo Eduse', 'Edusey',
    'Edusie', 'Krobo Edusey', 'Edusai', 'Krobo Edusia',
    'K. Edusie', 'Krobo Edusi', 'Edusei Krobo',
  ] },
  { canonical: 'Kwaku Boateng', role: 'Min. of Interior (1960–1964)', aliases: [
    'K. Boateng', 'Kwaku Boateng', 'Boateng', 'Kweku Boateng',
    'K. Boateng Interior', 'Boateng Kwaku', 'Boatang',
    'Kwaku Boatang', 'Boateng K.', 'Kweku Boatang', 'Boating',
  ] },
  { canonical: 'Ebenezer Ako-Adjei', role: 'Min. of Foreign Affairs (1959–1962)', aliases: [
    'Ako-Adjei', 'Ako Adjei', 'Ebenezer Ako Adjei', 'E. Ako-Adjei',
    'Akoadjei', 'Ako Adjie', 'Aco Adjei', 'Ebenezer Adjei',
    'Ako-Adgei', 'Akoajei', 'Akoh Adjei', 'Ako-Adjey',
  ] },
  { canonical: 'Tawia Adamafio', role: 'Min. of Information (1960–1962)', aliases: [
    'Adamafio', 'Tawia Adamafio', 'Tawiah Adamafio', 'T. Adamafio',
    'Adamafyo', 'Tawia Adamafo', 'Adamafeo', 'Tawiah Adamafo',
    'Adamafi', 'Tawia Adamafi', 'Adamafio Tawia', 'Adamafio T.',
  ] },
  { canonical: 'Kofi Baako', role: 'Min. of Defence (1960–1966)', aliases: [
    'Baako', 'Kofi Baako Jr', 'K. Baako', 'Kofi Baako Junior',
    'Bako', 'Kofi Bako', 'Baako Jr', 'Baako Kofi',
    'K. Bako', 'Kofi Barko', 'Bakoo', 'Kofi Bakoo',
  ] },
  { canonical: 'Alex Quaison-Sackey', role: 'Min. of Foreign Affairs (1965–1966)', aliases: [
    'Quaison-Sackey', 'Alex Quaison Sackey', 'Quaison Sackey',
    'A. Quaison-Sackey', 'Quaison-Sacky', 'Quason Sackey',
    'Alex Quason-Sackey', 'Kwayson Sackey', 'Quaisson Sackey',
    'Alex Quaison', 'Quaison Sacky', 'Kwayson-Sackey',
  ] },
  { canonical: 'Kwesi Armah', role: 'Min. of Trade (1961–1965)', aliases: [
    'Armah', 'Kwesi Armah', 'K. Armah', 'Kwesi Arma', 'Armaa',
    'Kwesi Armaa', 'Armahr', 'K. Arma', 'Kwesi Armar',
    'Kwasi Armah', 'Armah Kwesi', 'Arma Kwesi',
  ] },
  { canonical: 'Imoru Egala', role: 'Min. of Trade (1957–1961)', aliases: [
    'Egala', 'Imoru Egala', 'I. Egala', 'Imoru Igala', 'Igala',
    'Emoru Egala', 'Imoru Egalah', 'Egala Imoru', 'Egalah',
    'Imoru Egale', 'Imuru Egala', 'Egaler',
  ] },
  { canonical: 'A.E.A. Ofori Atta', role: 'Min. of Local Government', aliases: [
    'Ofori Atta', 'A.E.A. Ofori Atta', 'AEA Ofori Atta',
    'Ofori-Atta', 'A.E.A. Ofori-Atta', 'Oforiatta',
    'Ofori Ata', 'A.E.A. Ofori Ata', 'Ofori Attah',
    'AEA Ofori-Atta', 'A. Ofori Atta', 'Ofori Atta AEA',
  ] },
  { canonical: 'Susana Al-Hassan', role: 'Min. of Social Welfare (1961–1966)', aliases: [
    'Al-Hassan', 'Susana Alhassan', 'Susana Al Hassan',
    'S. Al-Hassan', 'Suzana Al-Hassan', 'Al Hassan Susana',
    'Alhassan', 'Susana Alhasan', 'Susanah Al-Hassan',
    'Al-Hasan', 'Susana Alhasaan', 'Al-Hassan Susana',
  ] },
  { canonical: 'Jatoe Kaleo', role: 'Min. of Agriculture (1962–1966)', aliases: [
    'Kaleo', 'Jatoe Kaleo', 'J. Kaleo', 'Jato Kaleo', 'Kalio',
    'Jatoe Kalio', 'Jato Kalio', 'Kaleo Jatoe', 'J. Kalio',
    'Jatoe Kaleyo', 'Kaleo J.', 'Jatoe Kallio',
  ] },
  { canonical: 'N.A. Welbeck', role: 'Min. of Works (1960–1966)', aliases: [
    'Welbeck', 'Nathaniel Welbeck', 'N.A. Welbeck', 'NA Welbeck',
    'Welbek', 'N. Welbeck', 'Nathaniel Welbek', 'Welbeck N.A.',
    'Welbeck Nathaniel', 'N.A. Welbek', 'Welbec', 'Welbeck NA',
  ] },
];

// ---------------------------------------------------------------------------
// Second Republic — Busia Government (1969–1972)
// ---------------------------------------------------------------------------

const SECOND_REPUBLIC_MINISTERS = [
  { canonical: 'J.H. Mensah', role: 'Min. of Finance (1969–1972)', aliases: [
    'J.H. Mensah', 'Joseph Henry Mensah', 'JH Mensah', 'J. H. Mensah',
    'Mensah J.H.', 'Joseph Mensah', 'Joe Mensah', 'J.H Mensah',
    'J.H. Mensa', 'Joseph H. Mensah', 'Mensah Joseph', 'JH Mensa',
  ] },
  { canonical: 'William Ofori-Atta', role: 'Min. of Foreign Affairs (1969–1972)', aliases: [
    'Paa Willie', 'Ofori-Atta', 'William Ofori Atta', 'W. Ofori-Atta',
    'Pa Willie', 'Ofori Atta William', 'William Ofori-Ata',
    'Paa Willy', 'Pa Willy', 'W. Ofori Atta', 'Oforiatta William',
  ] },
  { canonical: 'Victor Owusu', role: 'Attorney General (1969–1972)', aliases: [
    'Victor Owusu', 'V. Owusu', 'Owusu Victor', 'Victor Owusu AG',
    'Victor Owuzu', 'Victor Owosu', 'V. Owuzu', 'Owusu V.',
    'Victor Owusey', 'Owusu Attorney General', 'Victor Osu',
  ] },
  { canonical: 'R.R. Amponsah', role: 'Min. of Interior (1969–1972)', aliases: [
    'Amponsah', 'R.R. Amponsah', 'RR Amponsah', 'R. Amponsah',
    'Amponsa', 'R.R. Amponsa', 'Amponsaa', 'Amponsah R.R.',
    'Reginald Amponsah', 'R. R. Amponsah', 'Amponsa R.R.',
  ] },
  { canonical: 'Jones Ofori-Atta', role: 'Min. of Education (1969–1972)', aliases: [
    'Jones Ofori Atta', 'Jones Ofori-Atta', 'J. Ofori-Atta',
    'Jones Oforiatta', 'Jones Ofori Ata', 'Ofori-Atta Jones',
    'J. Ofori Atta', 'Jones Ofori', 'Ofori Atta Jones',
    'Jones Ofori-Ata', 'J Ofori-Atta', 'Jones O. Atta',
  ] },
  { canonical: 'Albert Adomakoh', role: 'Min. of Health (1969–1972)', aliases: [
    'Dr Adomakoh', 'Albert Adomakoh', 'Adomakoh', 'A. Adomakoh',
    'Adomako', 'Albert Adomako', 'Dr. Adomakoh', 'Adomakoh Albert',
    'Adomakoh Dr', 'Albert Adomako Dr', 'A. Adomako', 'Adomakor',
  ] },
  { canonical: 'A.A. Munufie', role: 'Min. of Agriculture (1969–1972)', aliases: [
    'Munufie', 'A.A. Munufie', 'AA Munufie', 'A. Munufie',
    'Munufi', 'Munufye', 'A.A. Munufi', 'Munufie A.A.',
    'Munufie AA', 'A.A Munufie', 'Munufie Agriculture',
  ] },
  { canonical: 'Jatoe Kaleo', role: 'Min. of Communications (1969–1972)', aliases: [
    'Kaleo', 'Jatoe Kaleo', 'J. Kaleo', 'Jato Kaleo', 'Kalio',
    'Jatoe Kalio', 'Jato Kalio', 'Kaleo Jatoe', 'J. Kalio',
    'Jatoe Kaleyo', 'Kaleo J.', 'Jatoe Kallio',
  ] },
];

// ---------------------------------------------------------------------------
// Third Republic — Limann Government (1979–1981)
// ---------------------------------------------------------------------------

const THIRD_REPUBLIC_MINISTERS = [
  { canonical: 'Amon Nikoi', role: 'Min. of Finance (1979–1981)', aliases: [
    'Nikoi', 'Amon Nikoi', 'A. Nikoi', 'Amon Nikoy', 'Nikoy',
    'Amon Nikoii', 'Nikoi Amon', 'A. Nikoy', 'Amon Nikkoi',
    'Nikoi Finance', 'Amon Nicoi', 'Nikoie',
  ] },
  { canonical: 'Isaac Chinebuah', role: 'Min. of Foreign Affairs (1979–1981)', aliases: [
    'Chinebuah', 'Isaac Chinebuah', 'I. Chinebuah', 'Chinebua',
    'Isaac Chinebua', 'Chinebuah Isaac', 'Chinebuahr',
    'I. Chinebua', 'Isaac Chinebuahr', 'Chinebuar', 'Chinebuah I.',
  ] },
  { canonical: 'Enoch Kwaku Okoh', role: 'Attorney General (1979–1981)', aliases: [
    'E.K. Okoh', 'Enoch Okoh', 'Kwaku Okoh', 'Okoh', 'E. K. Okoh',
    'Enoch Kwaku Oko', 'EK Okoh', 'Oko Enoch', 'E.K. Oko',
    'Okoh Attorney General', 'Enoch K. Okoh', 'Okoh EK',
  ] },
  { canonical: 'Imoru Egala', role: 'Min. of Agriculture (1979–1981)', aliases: [
    'Egala', 'Imoru Egala', 'I. Egala', 'Imoru Igala', 'Igala',
    'Emoru Egala', 'Imoru Egalah', 'Egala Imoru', 'Egalah',
    'Imoru Egale', 'Imuru Egala', 'Egaler',
  ] },
];

// ---------------------------------------------------------------------------
// PNDC Era — Rawlings Government (1982–1993)
// ---------------------------------------------------------------------------

const PNDC_MINISTERS = [
  { canonical: 'Kwesi Botchwey', role: 'Sec. for Finance (1982–1995)', aliases: [
    'Botchwey', 'Dr Botchwey', 'Kwesi Botchwey', 'K. Botchwey',
    'Botchway', 'Kwesi Botchway', 'Dr. Botchwey', 'Botchwey Kwesi',
    'Dr Botchway', 'Kwasi Botchwey', 'Botchwey Dr', 'K. Botchway',
  ] },
  { canonical: 'Obed Asamoah', role: 'Sec. for Foreign Affairs (1982–1993)', aliases: [
    'Obed Asamoah', 'Dr Asamoah', 'Asamoah', 'O. Asamoah',
    'Obed Asamoa', 'Dr. Asamoah', 'Asamoah Obed', 'Dr Asamoa',
    'Obed Asamoah Dr', 'O. Asamoa', 'Asamoah Dr', 'Obid Asamoah',
  ] },
  { canonical: 'Ato Austin', role: 'Sec. for Trade (1982–1986)', aliases: [
    'Ato Austin', 'A. Austin', 'Austin', 'Ato Austen', 'Austen',
    'Ato Austin Trade', 'Austin Ato', 'A. Austen', 'Ato Ostin',
    'Austin Secretary', 'Ato Austine', 'Atto Austin',
  ] },
  { canonical: 'P.V. Obeng', role: 'Chairman PNDC Committee (1982–1993)', aliases: [
    'P.V. Obeng', 'Obeng', 'PV Obeng', 'P. V. Obeng', 'P.V Obeng',
    'Obeng P.V.', 'Obeng PV', 'Peter Obeng', 'P.V. Obeng Chairman',
    'Obeng Chairman', 'PV Obeng PNDC', 'P.V. Obeing',
  ] },
  { canonical: 'Tsatsu Tsikata', role: 'Nat. Security Adviser (1982–2001)', aliases: [
    'Tsatsu', 'Captain Tsikata', 'Tsatsu Tsikata', 'Tsikata',
    'Capt Tsikata', 'Tsatsu Sikata', 'T. Tsikata', 'Satsu Tsikata',
    'Tsatsu Chikata', 'Tsikata Tsatsu', 'Capt. Tsikata', 'Tsatsu T.',
  ] },
  { canonical: 'Courage Quashigah', role: 'Sec. for Agriculture', aliases: [
    'Quashigah', 'Major Quashigah', 'Courage Quashigah', 'C. Quashigah',
    'Quashiga', 'Kwashigah', 'Major Quashiga', 'Quashigah Courage',
    'Maj Quashigah', 'Courage Quashiga', 'Quashigah Major', 'Quashigahr',
  ] },
  { canonical: 'Ama Ata Aidoo', role: 'Sec. for Education (1982–1983)', aliases: [
    'Ata Aidoo', 'Prof Ata Aidoo', 'Ama Ata Aidoo', 'A. Ata Aidoo',
    'Aidoo', 'Ama Ata', 'Ata Aido', 'Prof. Ata Aidoo',
    'Ama Ata Aido', 'Ata Aidoo Ama', 'Aidoo Ama', 'A.A. Aidoo',
  ] },
  { canonical: 'Mary Grant', role: 'Sec. for Local Government', aliases: [
    'Mary Grant', 'M. Grant', 'Grant', 'Mary Ghrant', 'Mary Grent',
    'Grant Mary', 'M. Ghrant', 'Mary Grant Secretary',
    'Mary Grant Local Government', 'Grant M.', 'Mary Grant', 'Marry Grant',
  ] },
  { canonical: 'E.T. Mensah', role: 'Sec. for Youth (1984–1993)', aliases: [
    'E.T. Mensah', 'ET Mensah', 'E. T. Mensah', 'Enoch Teye Mensah',
    'E.T Mensah', 'ET Mensa', 'Mensah ET', 'E.T. Mensa',
    'Enoch Mensah', 'E.T. Mensah Youth', 'Mensah E.T.', 'ET Mensahr',
  ] },
];

// ---------------------------------------------------------------------------
// Fourth Republic — Rawlings (NDC) Administration (1993–2001)
// ---------------------------------------------------------------------------

const RAWLINGS_MINISTERS = [
  { canonical: 'Kwesi Botchwey', role: 'Min. of Finance (1993–1995)', aliases: [
    'Botchwey', 'Kwesi Botchwey', 'K. Botchwey', 'Kwasi Botchwey',
    'Botchway', 'Kwesi Botchway', 'K. Botchway', 'Botchwey Kwesi',
    'Dr Botchwey', 'Dr. Botchwey', 'Kwesi Bochuey', 'Botchwei',
  ] },
  { canonical: 'Richard Kwame Peprah', role: 'Min. of Finance (1995–2001)', aliases: [
    'Peprah', 'Richard Peprah', 'Kwame Peprah', 'R.K. Peprah',
    'R. Peprah', 'Pepra', 'Richard Kwame Pepra', 'Peprah Richard',
    'Kwame Pepra', 'Richard Pepra', 'Peprah Finance', 'RK Peprah',
  ] },
  { canonical: 'Obed Asamoah', role: 'Min. of Foreign Affairs (1993–1997)', aliases: [
    'Obed Asamoah', 'Dr Asamoah', 'Asamoah', 'O. Asamoah',
    'Obed Asamoa', 'Dr. Asamoah', 'Asamoah Obed', 'Dr Asamoa',
    'Obed Asamoah Foreign', 'O. Asamoa', 'Asamoah Dr', 'Obid Asamoah',
  ] },
  { canonical: 'Victor Gbeho', role: 'Min. of Foreign Affairs (1997–2001)', aliases: [
    'Gbeho', 'Victor Gbeho', 'V. Gbeho', 'Gbeho Victor',
    'Gbecho', 'Victor Gbecho', 'Gbehor', 'V. Gbecho',
    'Victor Gbehor', 'Gbeho Foreign Affairs', 'Gbeo', 'Victor Gbeo',
  ] },
  { canonical: 'Nii Okaija Adamafio', role: 'Min. of Interior (1993–1997)', aliases: [
    'Nii Okaija', 'Adamafio', 'Nii Okaija Adamafio', 'N. Adamafio',
    'Okaija', 'Nii Okaija Interior', 'Okaidja Adamafio', 'Nii Okaidja',
    'Okaija Adamafio', 'Adamafio Interior', 'N. Okaija', 'Nii Okaja',
  ] },
  { canonical: 'Kwamena Ahwoi', role: 'Min. of Local Government (1993–2001)', aliases: [
    'Kwamena Ahwoi', 'K. Ahwoi', 'Ahwoi', 'Kwamena Ahwoy',
    'Kwamina Ahwoi', 'Ahwoy', 'Kwamena Ahwoi Local Govt',
    'Ahwoi Kwamena', 'K. Ahwoy', 'Ahwoi Local Government', 'Kwamena A.', 'Ahwoi K.',
  ] },
  { canonical: 'E.T. Mensah', role: 'Min. of Youth & Sports (1993–2001)', aliases: [
    'E.T. Mensah', 'ET Mensah', 'E. T. Mensah', 'Enoch Teye Mensah',
    'E.T Mensah', 'ET Mensa', 'Mensah ET', 'E.T. Mensa',
    'Enoch Mensah', 'E.T. Mensah Youth', 'Mensah E.T.', 'ET Mensahr',
  ] },
  { canonical: 'Christine Amoako-Nuama', role: 'Min. of Education (1997–2001)', aliases: [
    'Amoako-Nuama', 'Christine Amoako-Nuama', 'C. Amoako-Nuama',
    'Amoako Nuama', 'Christine Amoako Nuama', 'Amoakonuama',
    'Christine Nuama', 'Amoako-Nuamah', 'C. Amoako Nuama',
    'Christine Amoako', 'Amoako-Nuama Education', 'Nuama',
  ] },
  { canonical: 'Harry Sawyer', role: 'Attorney General (1993–1997)', aliases: [
    'Harry Sawyer', 'H. Sawyer', 'Sawyer', 'Harry Sayer',
    'Sawyer AG', 'Harry Sawya', 'Sawya', 'H. Sayer',
    'Sawyer Harry', 'Sawyer Attorney General', 'Harry S.', 'Sawyerr',
  ] },
  { canonical: 'Nana Oye Lithur', role: 'Dep. Attorney General (1997–2001)', aliases: [
    'Oye Lithur', 'Nana Oye Lithur', 'Lithur', 'N. Oye Lithur',
    'Nana Oye Litor', 'Oye Litor', 'Nana Lithur', 'Oye Lithur Nana',
    'N. Lithur', 'Lithur Nana', 'Nana Oye', 'Lithor',
  ] },
  { canonical: 'Samuel Sallas-Mensah', role: 'Min. of Interior (1997–2001)', aliases: [
    'Sallas-Mensah', 'Samuel Sallas-Mensah', 'S. Sallas-Mensah',
    'Sallas Mensah', 'Samuel Sallas Mensah', 'Salas Mensah',
    'Sallas Mensa', 'Sallas-Mensah Interior', 'Samuel Salas Mensah',
    'Sallasmensah', 'Sallas-Mensah Samuel', 'S. Salas Mensah',
  ] },
  { canonical: 'John Mahama', role: 'Min. of Communications (1997–2001)', aliases: [
    'John Mahama', 'J.D. Mahama', 'Mahama', 'J. Mahama',
    'John Dramani Mahama', 'Mahama John', 'JD Mahama',
    'John Mahama Communications', 'Mahama Communications',
    'J. D. Mahama', 'Mahama J.D.', 'John D. Mahama',
  ] },
  { canonical: 'Ibrahim Adam', role: 'Min. of Defence (1997–2001)', aliases: [
    'Ibrahim Adam', 'I. Adam', 'Adam Ibrahim', 'Ibrahim Adams',
    'Ibraheem Adam', 'Ibrahim Adamu', 'I. Adams', 'Adam Defence',
    'Ibrahim Adam Defence', 'Adams Ibrahim', 'Ibraham Adam', 'Ibrahim A.',
  ] },
  { canonical: 'Kwaku Afriyie', role: 'Min. of Health (1993–1997)', aliases: [
    'Kwaku Afriyie', 'K. Afriyie', 'Afriyie', 'Dr Afriyie',
    'Kwaku Afriye', 'Afriye', 'Dr. Afriyie', 'Afriyie Health',
    'Kweku Afriyie', 'Afriyie Kwaku', 'K. Afriye', 'Dr Afriye',
  ] },
  { canonical: 'Mustapha Idris', role: 'Min. of Roads (1993–1997)', aliases: [
    'Mustapha Idris', 'M. Idris', 'Idris', 'Mustapha Idrees',
    'Mustapha Iddrisu', 'Idris Mustapha', 'Mustapha I.', 'Idrees',
    'Idris Roads', 'Mustapha Idris Roads', 'M. Idrees', 'Mustafa Idris',
  ] },
  { canonical: 'Edward Salia', role: 'Min. of Mines & Energy (1997–2001)', aliases: [
    'Edward Salia', 'E. Salia', 'Salia', 'Edward Saliah',
    'Salia Edward', 'Ed Salia', 'Salia Mines', 'E. Saliah',
    'Edward Salia Mines', 'Salia Energy', 'Saliah', 'Edward S.',
  ] },
  { canonical: 'Cletus Avoka', role: 'Min. of Interior (2001)', aliases: [
    'Cletus Avoka', 'C. Avoka', 'Avoka', 'Cletus Avoca',
    'Avoka Cletus', 'Avoca', 'Cletus Avoka Interior', 'C. Avoca',
    'Avoka Interior', 'Cletus A.', 'Cletus Avokah', 'Avokah',
  ] },
];

// ---------------------------------------------------------------------------
// Fourth Republic — Kufuor (NPP) Administration (2001–2009)
// ---------------------------------------------------------------------------

const KUFUOR_MINISTERS = [
  { canonical: 'Yaw Osafo-Maafo', role: 'Min. of Finance (2001–2005)', aliases: [
    'Osafo-Maafo', 'Yaw Osafo-Maafo', 'Y. Osafo-Maafo', 'Osafo Maafo',
    'Osafo-Mafo', 'Yaw Osafo Maafo', 'Osafomaafo', 'Osafo-Maafo Yaw',
    'Y. Osafo Maafo', 'Osafo-Maafor', 'Yaw Osafo', 'Maafo',
  ] },
  { canonical: 'Kwadwo Baah-Wiredu', role: 'Min. of Finance (2005–2008)', aliases: [
    'Baah-Wiredu', 'Kwadwo Baah-Wiredu', 'K. Baah-Wiredu', 'Baah Wiredu',
    'Bah-Wiredu', 'Kwadwo Baah Wiredu', 'Baahwiredu', 'Wiredu',
    'Baah-Wiredu Finance', 'Kwadwo Wiredu', 'K. Baah Wiredu', 'Bah Wiredu',
  ] },
  { canonical: 'Anthony Akoto Osei', role: 'Min. of Finance (2008–2009)', aliases: [
    'Akoto Osei', 'Anthony Akoto Osei', 'A. Akoto Osei', 'Dr Akoto Osei',
    'Anthony Akoto', 'Akoto Osey', 'Dr. Akoto Osei', 'Akoto Osei Anthony',
    'Akoto Osei Finance', 'A. Akoto', 'Anthony Osei', 'Akoto Osey Anthony',
  ] },
  { canonical: 'Hackman Owusu-Agyemang', role: 'Min. of Foreign Affairs (2001–03) / Interior (2003–05)', aliases: [
    'Hackman Owusu-Agyemang', 'Hackman', 'Owusu-Agyemang', 'H. Owusu-Agyemang',
    'Hackman Owusu Agyemang', 'Hackman Owusu', 'Owusu Agyemang',
    'Hackman Owusuagyemang', 'H. Owusu Agyemang', 'Owusu-Agyemang Hackman',
    'Hackman Interior', 'Hacman Owusu-Agyemang',
  ] },
  { canonical: 'Nana Akufo-Addo', role: 'Min. of Foreign Affairs (2003–07) / AG (2001–03)', aliases: [
    'Akufo-Addo', 'Nana Akufo-Addo', 'N. Akufo-Addo', 'Akufo Addo',
    'Nana Addo', 'Akufoaddo', 'Nana Akufo Addo', 'Akufo-Ado',
    'Nana Akufoaddo', 'Akufo-Addo Foreign', 'Nana Addo AG', 'N. Addo',
  ] },
  { canonical: 'Akwasi Osei-Adjei', role: 'Min. of Foreign Affairs (2007–2009)', aliases: [
    'Osei-Adjei', 'Akwasi Osei-Adjei', 'A. Osei-Adjei', 'Osei Adjei',
    'Akwasi Osei Adjei', 'Oseiadjei', 'Osei-Adjei Foreign',
    'Akwasi Adjei', 'Osei-Adjey', 'A. Osei Adjei', 'Akwasi O.', 'Osei-Adgei',
  ] },
  { canonical: 'Papa Owusu-Ankomah', role: 'AG (2003–05) / Interior (2005–07) / Education (2001–03)', aliases: [
    'Owusu-Ankomah', 'Papa Owusu-Ankomah', 'P. Owusu-Ankomah',
    'Papa Owusu Ankomah', 'Owusu Ankomah', 'Owusuankomah',
    'Papa Ankomah', 'Owusu-Ankoma', 'Papa Owusu', 'P. Owusu Ankomah',
    'Owusu-Ankomah AG', 'Papa Owusu-Ankoma',
  ] },
  { canonical: 'Joe Ghartey', role: 'Attorney General (2005–2009)', aliases: [
    'Joe Ghartey', 'J. Ghartey', 'Ghartey', 'Joe Gartey',
    'Ghartey AG', 'Joe Ghartey AG', 'Ghartey Joe', 'J. Gartey',
    'Joseph Ghartey', 'Ghartei', 'Joe Ghartei', 'Ghartey Joseph',
  ] },
  { canonical: 'Kwamena Bartels', role: 'Min. of Interior (2007–09) / Information (2005–07)', aliases: [
    'Kwamena Bartels', 'K. Bartels', 'Bartels', 'Kwamena Bartles',
    'Kwamina Bartels', 'Bartels Interior', 'Bartles', 'Bartels Kwamena',
    'K. Bartles', 'Kwamena Bartels Interior', 'Bartels Information', 'Kwamena B.',
  ] },
  { canonical: 'Kwame Addo-Kufuor', role: 'Min. of Defence (2001–2007)', aliases: [
    'Addo-Kufuor', 'Kwame Addo-Kufuor', 'K. Addo-Kufuor', 'Dr Addo-Kufuor',
    'Addo Kufuor', 'Addokufuor', 'Kwame Addo Kufuor', 'Dr. Addo-Kufuor',
    'Addo-Kufuor Defence', 'Kwame Kufuor', 'K. Addo Kufuor', 'Addo-Kufour',
  ] },
  { canonical: 'Albert Kan-Dapaah', role: 'Min. of Defence (2007–09) / Energy (2001–03)', aliases: [
    'Kan-Dapaah', 'Albert Kan-Dapaah', 'A. Kan-Dapaah', 'Kan Dapaah',
    'Kandapaah', 'Albert Kan Dapaah', 'Kan-Dapaa', 'Kan-Dapaah Defence',
    'Albert Kandapaah', 'Kan-Dapaah Energy', 'A. Kan Dapaah', 'Kan-Dapa',
  ] },
  { canonical: 'Courage Quashigah', role: 'Min. of Health (2001–07) / Food & Agric (2007–09)', aliases: [
    'Quashigah', 'Major Quashigah', 'Courage Quashigah', 'C. Quashigah',
    'Quashiga', 'Kwashigah', 'Major Quashiga', 'Quashigah Health',
    'Maj Quashigah', 'Courage Quashiga', 'Quashigah Major', 'Quashigahr',
  ] },
  { canonical: 'Joseph Yieleh Chireh', role: 'Min. of Health (2007–2009)', aliases: [
    'Yieleh Chireh', 'Joseph Yieleh Chireh', 'J. Yieleh Chireh',
    'Yieleh', 'Chireh', 'Yieleh Chire', 'Joseph Chireh',
    'Yieleh Chireh Health', 'J. Chireh', 'Chireh Joseph', 'Yileh Chireh', 'Chire',
  ] },
  { canonical: 'Richard Anane', role: 'Min. of Roads (2001–2006)', aliases: [
    'Richard Anane', 'R. Anane', 'Anane', 'Dr Anane', 'Dr. Anane',
    'Anane Roads', 'Richard Anane Roads', 'Anane Richard',
    'Richard Anain', 'Anain', 'R. Anain', 'Anane Dr',
  ] },
  { canonical: 'Joe Gidisu', role: 'Min. of Roads (2006–2009)', aliases: [
    'Joe Gidisu', 'J. Gidisu', 'Gidisu', 'Joe Gidesu',
    'Gidisu Roads', 'Joe Gidisu Roads', 'Gidesu', 'Gidisu Joe',
    'J. Gidesu', 'Gidisu J.', 'Joe G.', 'Gidizu',
  ] },
  { canonical: 'Joseph Kofi Adda', role: 'Min. of Energy (2006–2009)', aliases: [
    'Kofi Adda', 'Joseph Kofi Adda', 'J.K. Adda', 'Adda',
    'Joseph Adda', 'Kofi Ada', 'Adda Energy', 'Kofi Adda Energy',
    'J. Adda', 'Adda Joseph', 'JK Adda', 'Ada',
  ] },
  { canonical: 'Alan Kyerematen', role: 'Min. of Trade (2003–2007)', aliases: [
    'Alan Kyerematen', 'A. Kyerematen', 'Kyerematen', 'Alan Cash',
    'Kyerematin', 'Alan Kyerematin', 'Kyerematen Trade', 'Alan K.',
    'Kyerematen Alan', 'A. Kyerematin', 'Alan Kyeremanten', 'Kyeremanten',
  ] },
  { canonical: 'Kofi Konadu Apraku', role: 'Min. of Trade (2001–2003)', aliases: [
    'Kofi Konadu Apraku', 'K.K. Apraku', 'Apraku', 'Dr Apraku',
    'Konadu Apraku', 'Dr. Apraku', 'K. Apraku', 'Apraku Trade',
    'Kofi Apraku', 'KK Apraku', 'Apraku Konadu', 'Konadu Apreku',
  ] },
  { canonical: 'Kasim Kasanga', role: 'Min. of Lands (2001–2005)', aliases: [
    'Kasim Kasanga', 'K. Kasanga', 'Kasanga', 'Kassim Kasanga',
    'Kasanga Lands', 'Kasim Kassanga', 'Kasanga Kasim', 'K. Kassanga',
    'Kasim K.', 'Cassim Kasanga', 'Kasanga K.', 'Kasim Kasangah',
  ] },
  { canonical: 'Dominic Fobih', role: 'Min. of Lands (2005–2009)', aliases: [
    'Dominic Fobih', 'D. Fobih', 'Fobih', 'Dominic Fobi',
    'Fobih Lands', 'Dominic Fobih Lands', 'Fobi', 'Fobih Dominic',
    'D. Fobi', 'Fobih D.', 'Dominic F.', 'Fobihr',
  ] },
  { canonical: 'Jake Obetsebi-Lamptey', role: 'Min. of Information (2001–03) / Tourism (2003–09)', aliases: [
    'Jake Obetsebi-Lamptey', 'Obetsebi-Lamptey', 'J. Obetsebi-Lamptey',
    'Jake Obetsebi Lamptey', 'Obetsebi Lamptey', 'Jake Lamptey',
    'Obetsebilamptey', 'Obetsebi-Lamptey Tourism', 'Jake Obetsebi',
    'J. Lamptey', 'Obetsebi-Lamptey Jake', 'Obetsebi-Lamptei',
  ] },
  { canonical: 'Nana Akomea', role: 'Min. of Information (2003–2005)', aliases: [
    'Nana Akomea', 'N. Akomea', 'Akomea', 'Nana Akomia',
    'Akomea Information', 'Nana Akomea Information', 'Akomiah',
    'Akomea Nana', 'N. Akomia', 'Akomea N.', 'Nana A.', 'Akomeah',
  ] },
  { canonical: 'Alhaji Abubakar Saddique Boniface', role: 'Min. of Works (2001–2009)', aliases: [
    'Saddique Boniface', 'Abubakar Boniface', 'Boniface', 'A. Boniface',
    'Saddique', 'Alhaji Boniface', 'Saddick Boniface', 'Sadique Boniface',
    'Abubakar Saddique', 'Boniface Works', 'Saddique Bonface', 'Bonifas',
  ] },
  { canonical: 'Cecilia Johnson', role: 'Min. of Women & Children (2001–2005)', aliases: [
    'Cecilia Johnson', 'C. Johnson', 'Johnson', 'Cecilia Jonson',
    'Cecilia Johnson Women', 'Johnson Women', 'Cecelia Johnson',
    'Cecilia J.', 'Johnson Cecilia', 'C. Jonson', 'Cecilia Johson', 'Jonson',
  ] },
  { canonical: 'Hajia Alima Mahama', role: 'Min. of Women & Children (2005–2009)', aliases: [
    'Alima Mahama', 'Hajia Alima Mahama', 'H. Alima Mahama', 'Alima',
    'Hajia Alima', 'Alima Mama', 'Alima Mahama Women', 'Hajia Mahama',
    'Alima Mahama Hajia', 'A. Mahama', 'Alimah Mahama', 'Hajia Alimah',
  ] },
  { canonical: 'Kwadwo Adjei-Darko', role: 'Min. of Local Govt (2001–2005)', aliases: [
    'Adjei-Darko', 'Kwadwo Adjei-Darko', 'K. Adjei-Darko', 'Adjei Darko',
    'Kwadwo Adjei Darko', 'Adjeidarko', 'Darko', 'Adjei-Darko Local',
    'Kwadwo Darko', 'Adjei-Darko Kwadwo', 'K. Adjei Darko', 'Adjei-Darco',
  ] },
  { canonical: 'Stephen Asamoah Boateng', role: 'Min. of Local Govt (2005–2009)', aliases: [
    'Asamoah Boateng', 'Stephen Asamoah Boateng', 'S. Asamoah Boateng',
    'Asabee', 'Asamoah Boateng Local', 'Stephen Asamoah', 'S.A. Boateng',
    'Asamoah Boatang', 'Asamoa Boateng', 'Asamoah Boateng Stephen',
    'Boateng Stephen', 'SA Boateng',
  ] },
];


// ---------------------------------------------------------------------------
// Fourth Republic — Mills/Mahama (NDC) Administration (2009–2017)
// ---------------------------------------------------------------------------

const MILLS_MAHAMA_MINISTERS = [
  { canonical: 'Kwabena Duffuor', role: 'Min. of Finance (2009–2012)', aliases: [
    'Duffuor', 'Kwabena Duffuor', 'K. Duffuor', 'Dr Duffuor',
    'Dufuor', 'Dr. Duffuor', 'Kwabena Dufuor', 'Duffuor Finance',
    'Duffuor Kwabena', 'K. Dufuor', 'Duffour', 'Kwabena Duffour',
  ] },
  { canonical: 'Seth Terkper', role: 'Min. of Finance (2012–2017)', aliases: [
    'Seth Terkper', 'S. Terkper', 'Terkper', 'Seth Tekper',
    'Terkper Finance', 'Terkpeh', 'Seth Terkpeh', 'Terkper Seth',
    'S. Tekper', 'Seth T.', 'Tekper', 'Terkper S.',
  ] },
  { canonical: 'Alhaji Muhammad Mumuni', role: 'Min. of Foreign Affairs (2009–2013)', aliases: [
    'Muhammad Mumuni', 'Mumuni', 'Alhaji Mumuni', 'M. Mumuni',
    'Mohammed Mumuni', 'Mumuni Foreign', 'Muhammad Mumunee',
    'Alhaji Muhammad Mumuni', 'Mumuni Muhammad', 'Mumunee', 'M. Mumunee', 'Mumuni M.',
  ] },
  { canonical: 'Hanna Tetteh', role: 'Min. of Foreign Affairs (2013–17) / Trade (2009–13)', aliases: [
    'Hanna Tetteh', 'H. Tetteh', 'Tetteh', 'Hannah Tetteh',
    'Hanna Tete', 'Tetteh Foreign', 'Hanna Tetteh Trade',
    'Tetteh Hanna', 'H. Tete', 'Tetteh Hannah', 'Tetteh H.', 'Hanna T.',
  ] },
  { canonical: 'Betty Mould-Iddrisu', role: 'AG (2009–10) / Education (2010–11)', aliases: [
    'Betty Mould-Iddrisu', 'Mould-Iddrisu', 'B. Mould-Iddrisu',
    'Betty Mould Iddrisu', 'Mould Iddrisu', 'Betty Mould',
    'Mouldiddrisu', 'Mould-Idrisu', 'Betty Mould AG', 'B. Mould',
    'Mould-Iddrisu Betty', 'Betty Mould-Idrisu',
  ] },
  { canonical: 'Martin Amidu', role: 'Attorney General (2010–2012)', aliases: [
    'Martin Amidu', 'M. Amidu', 'Amidu', 'Martin Amidu AG',
    'Amidu Martin', 'Martin Amedo', 'Amedo', 'Amidu AG',
    'M. Amedo', 'Martin A.', 'Amidu M.', 'Amiidu',
  ] },
  { canonical: 'Marietta Brew Appiah-Oppong', role: 'Attorney General (2013–2017)', aliases: [
    'Marietta Brew', 'Brew Appiah-Oppong', 'M. Brew Appiah-Oppong',
    'Marietta Brew Appiah-Oppong', 'Brew Appiah Oppong', 'Marietta AG',
    'Brew Appiah-Opong', 'Marietta Brew AG', 'Appiah-Oppong',
    'Brew Appiah', 'Marietta Brew Appiah Oppong', 'M. Brew',
  ] },
  { canonical: 'Cletus Avoka', role: 'Min. of Interior (2009–2010)', aliases: [
    'Cletus Avoka', 'C. Avoka', 'Avoka', 'Cletus Avoca',
    'Avoka Interior', 'Cletus Avoka Interior', 'Avoca', 'Avoka Cletus',
    'C. Avoca', 'Avokah', 'Cletus Avokah', 'Avoka C.',
  ] },
  { canonical: 'Benjamin Kunbuor', role: 'Min. of Interior (2010–12) / Defence (2012–13)', aliases: [
    'Kunbuor', 'Benjamin Kunbuor', 'B. Kunbuor', 'Dr Kunbuor',
    'Kunbour', 'Dr. Kunbuor', 'Benjamin Kunbour', 'Kunbuor Interior',
    'Kunbuor Defence', 'B. Kunbour', 'Kunbuor Benjamin', 'Kunbor',
  ] },
  { canonical: 'Mark Woyongo', role: 'Min. of Interior (2013–2017)', aliases: [
    'Mark Woyongo', 'M. Woyongo', 'Woyongo', 'Mark Woyongoh',
    'Woyongo Interior', 'Woyongoh', 'Woyongo Mark', 'M. Woyongoh',
    'Woyongo M.', 'Mark W.', 'Wayongo', 'Mark Wayongo',
  ] },
  { canonical: 'J.E. Smith', role: 'Min. of Defence (2009–2011)', aliases: [
    'J.E. Smith', 'JE Smith', 'J. E. Smith', 'Smith Defence',
    'Lt Gen Smith', 'Lt. Gen. Smith', 'Smith', 'J.E Smith',
    'Smith J.E.', 'General Smith', 'Smith JE', 'JE Smith Defence',
  ] },
  { canonical: 'Alex Tettey-Enyo', role: 'Min. of Education (2009–2010)', aliases: [
    'Tettey-Enyo', 'Alex Tettey-Enyo', 'A. Tettey-Enyo', 'Tettey Enyo',
    'Alex Tettey Enyo', 'Tetteyenyo', 'Tettey-Enyo Education',
    'Alex Enyo', 'Tettey-Enyo Alex', 'A. Tettey Enyo', 'Enyo', 'Tetey-Enyo',
  ] },
  { canonical: 'Lee Ocran', role: 'Min. of Education (2011–2012)', aliases: [
    'Lee Ocran', 'L. Ocran', 'Ocran', 'Lee Okran',
    'Ocran Education', 'Lee Ocran Education', 'Okran', 'Ocran Lee',
    'L. Okran', 'Lee O.', 'Ocran L.', 'Lee Ochran',
  ] },
  { canonical: 'Jane Naana Opoku-Agyemang', role: 'Min. of Education (2013–2017)', aliases: [
    'Naana Opoku-Agyemang', 'Jane Naana', 'Prof Opoku-Agyemang',
    'J. Opoku-Agyemang', 'Opoku-Agyemang', 'Naana Jane',
    'Prof. Opoku-Agyemang', 'Opoku Agyemang', 'Naana Opoku Agyemang',
    'Opoku-Agyemang Education', 'Jane Opoku-Agyemang', 'Naana Opoku',
  ] },
  { canonical: 'Sherry Ayittey', role: 'Min. of Health (2010–2013)', aliases: [
    'Sherry Ayittey', 'S. Ayittey', 'Ayittey', 'Sherry Ayitey',
    'Ayittey Health', 'Sherry Ayittey Health', 'Ayitey', 'Ayittey Sherry',
    'S. Ayitey', 'Sherry A.', 'Ayittey S.', 'Sherry Ayiti',
  ] },
  { canonical: 'Alex Segbefia', role: 'Min. of Health (2013–2017)', aliases: [
    'Alex Segbefia', 'A. Segbefia', 'Segbefia', 'Alex Segbefia Health',
    'Segbefiah', 'Segbefia Health', 'Alex Segbefiah', 'Segbefia Alex',
    'A. Segbefiah', 'Alex S.', 'Segbefia A.', 'Sekbefia',
  ] },
  { canonical: 'Joe Gidisu', role: 'Min. of Roads (2009–2012)', aliases: [
    'Joe Gidisu', 'J. Gidisu', 'Gidisu', 'Joe Gidesu',
    'Gidisu Roads', 'Gidesu', 'Gidisu Joe', 'J. Gidesu',
    'Gidisu J.', 'Joe G.', 'Gidizu', 'Joe Gidisu Roads',
  ] },
  { canonical: 'Inusah Fuseini', role: 'Min. of Roads (2012–2013)', aliases: [
    'Inusah Fuseini', 'I. Fuseini', 'Fuseini', 'Inusah Fuseni',
    'Fuseini Roads', 'Inusah Fuseini Roads', 'Fuseni', 'Fuseini Inusah',
    'I. Fuseni', 'Inusah F.', 'Fuseini I.', 'Inusa Fuseini',
  ] },
  { canonical: 'Alhaji Collins Dauda', role: 'Min. of Roads (2013–14) / Lands (2009–13) / Works (2013–17)', aliases: [
    'Collins Dauda', 'Alhaji Collins Dauda', 'C. Dauda', 'Dauda',
    'Collins Dauda Roads', 'Collins Dauda Lands', 'Dauda Works',
    'Collins Dawuda', 'Alhaji Dauda', 'Dauda Collins', 'Dawuda', 'C. Dawuda',
  ] },
  { canonical: 'Sui Nyantakyi', role: 'Min. of Roads (2014–2017)', aliases: [
    'Sui Nyantakyi', 'S. Nyantakyi', 'Nyantakyi', 'Sui Nyantakey',
    'Nyantakyi Roads', 'Sui Nyantakyi Roads', 'Nyantakey', 'Nyantakyi Sui',
    'S. Nyantakey', 'Sui N.', 'Nyantakyi S.', 'Nyantaki',
  ] },
  { canonical: 'Joe Oteng-Adjei', role: 'Min. of Energy (2009–2013)', aliases: [
    'Oteng-Adjei', 'Joe Oteng-Adjei', 'J. Oteng-Adjei', 'Oteng Adjei',
    'Joe Oteng Adjei', 'Otengadjei', 'Oteng-Adjei Energy',
    'Joe Oteng', 'Oteng-Adjei Joe', 'J. Oteng Adjei', 'Oteng-Adjey', 'Oteng',
  ] },
  { canonical: 'Emmanuel Armah-Kofi Buah', role: 'Min. of Energy (2013–2017)', aliases: [
    'Armah-Kofi Buah', 'Emmanuel Buah', 'E. Buah', 'Buah',
    'Armah Kofi Buah', 'Emmanuel Armah-Kofi Buah', 'Buah Energy',
    'Armah-Kofi', 'Buah Emmanuel', 'E. Armah-Kofi Buah', 'Armah Kofi', 'Buahr',
  ] },
  { canonical: 'Ekwow Spio-Garbrah', role: 'Min. of Trade (2013–2017)', aliases: [
    'Spio-Garbrah', 'Ekwow Spio-Garbrah', 'E. Spio-Garbrah',
    'Spio Garbrah', 'Ekwow Spio Garbrah', 'Spiogarbrah',
    'Spio-Garbra', 'Spio-Garbrah Trade', 'Ekwow Spio', 'E. Spio Garbrah',
    'Spio-Garbrah Ekwow', 'Spio-Garba',
  ] },
  { canonical: 'Nii Osah Mills', role: 'Min. of Lands (2013–2017)', aliases: [
    'Nii Osah Mills', 'N. Osah Mills', 'Osah Mills', 'Nii Osah',
    'Osah Mills Lands', 'Nii Mills', 'Osa Mills', 'Nii Osah Mils',
    'N. Mills', 'Osah Mills Nii', 'Nii Osa Mills', 'Osah Mils',
  ] },
  { canonical: 'Zita Okaikoi', role: 'Min. of Information (2009–11) / Tourism (2011–13)', aliases: [
    'Zita Okaikoi', 'Z. Okaikoi', 'Okaikoi', 'Zita Okaikoy',
    'Okaikoi Information', 'Okaikoi Tourism', 'Okaikoy', 'Okaikoi Zita',
    'Z. Okaikoy', 'Zita O.', 'Okaikoi Z.', 'Zita Okaikwei',
  ] },
  { canonical: 'Fritz Baffour', role: 'Min. of Information (2011–2013)', aliases: [
    'Fritz Baffour', 'F. Baffour', 'Baffour', 'Fritz Bafor',
    'Baffour Information', 'Fritz Baffour Information', 'Bafor',
    'Baffour Fritz', 'F. Bafor', 'Fritz B.', 'Baffour F.', 'Baffor',
  ] },
  { canonical: 'Mahama Ayariga', role: 'Min. of Information (2013–14) / Youth (2014–17)', aliases: [
    'Mahama Ayariga', 'M. Ayariga', 'Ayariga', 'Mahama Ayarigah',
    'Ayariga Information', 'Ayariga Youth', 'Ayarigah', 'Ayariga Mahama',
    'M. Ayarigah', 'Mahama A.', 'Ayariga M.', 'Ayareeqa',
  ] },
  { canonical: 'Haruna Iddrisu', role: 'Min. of Communications (2009–13) / Employment (2014–17)', aliases: [
    'Haruna Iddrisu', 'H. Iddrisu', 'Iddrisu', 'Haruna Idrisu',
    'Iddrisu Communications', 'Iddrisu Employment', 'Idrisu',
    'Iddrisu Haruna', 'H. Idrisu', 'Haruna I.', 'Iddrisu H.', 'Harouna Iddrisu',
  ] },
  { canonical: 'Edward Omane Boamah', role: 'Min. of Communications (2013–2017)', aliases: [
    'Omane Boamah', 'Edward Omane Boamah', 'E. Omane Boamah',
    'Omane Boama', 'Edward Omane', 'Omaneboamah', 'Boamah',
    'Omane Boamah Communications', 'Dr Omane Boamah', 'Dr. Omane Boamah',
    'Omane Boamah Edward', 'E. Boamah',
  ] },
  { canonical: 'Enoch Teye Mensah', role: 'Min. of Works (2009–13) / Employment (2013–14)', aliases: [
    'E.T. Mensah', 'Enoch Teye Mensah', 'ET Mensah', 'E. T. Mensah',
    'E.T Mensah', 'ET Mensa', 'Mensah Works', 'E.T. Mensah Works',
    'Mensah Employment', 'Enoch Mensah', 'E.T. Mensa', 'ET Mensahr',
  ] },
  { canonical: 'Samuel Ofosu-Ampofo', role: 'Min. of Local Govt (2010–2013)', aliases: [
    'Ofosu-Ampofo', 'Samuel Ofosu-Ampofo', 'S. Ofosu-Ampofo',
    'Ofosu Ampofo', 'Ofosuampofo', 'Samuel Ofosu Ampofo',
    'Ofosu-Ampofo Local', 'S. Ofosu Ampofo', 'Ofosu-Ampfo',
    'Ofosu-Ampofo Samuel', 'Samuel Ampofo', 'Ofosu-Ampofor',
  ] },
  { canonical: 'Julius Debrah', role: 'Min. of Local Govt (2013–2017)', aliases: [
    'Julius Debrah', 'J. Debrah', 'Debrah', 'Julius Debra',
    'Debrah Local Govt', 'Julius Debrah Local', 'Debra', 'Debrah Julius',
    'J. Debra', 'Julius D.', 'Debrah J.', 'Debrahr',
  ] },
  { canonical: 'Kwesi Ahwoi', role: 'Min. of Food & Agric (2009–2013)', aliases: [
    'Kwesi Ahwoi', 'K. Ahwoi', 'Ahwoi', 'Kwesi Ahwoy',
    'Ahwoi Agriculture', 'Kwesi Ahwoi Agriculture', 'Ahwoy', 'Ahwoi Kwesi',
    'K. Ahwoy', 'Kwesi A.', 'Ahwoi K.', 'Kwesi Ahwoih',
  ] },
  { canonical: 'Clement Kofi Humado', role: 'Min. of Food & Agric (2013–14) / Youth (2009–11)', aliases: [
    'Clement Humado', 'C. Humado', 'Humado', 'Kofi Humado',
    'Humado Agriculture', 'Humado Youth', 'Clement Kofi Humado',
    'Humado Clement', 'C.K. Humado', 'Humadoh', 'K. Humado', 'CK Humado',
  ] },
  { canonical: 'Elizabeth Ofosu-Agyare', role: 'Min. of Tourism (2013–2017)', aliases: [
    'Ofosu-Agyare', 'Elizabeth Ofosu-Agyare', 'E. Ofosu-Agyare',
    'Ofosu Agyare', 'Elizabeth Ofosu Agyare', 'Ofosuagyare',
    'Ofosu-Agyare Tourism', 'Elizabeth Agyare', 'E. Ofosu Agyare',
    'Ofosu-Agyare Elizabeth', 'Ofosu-Agyareh', 'Agyare',
  ] },
  { canonical: 'Juliana Azumah-Mensah', role: 'Min. of Gender (2009–2012)', aliases: [
    'Juliana Azumah-Mensah', 'Azumah-Mensah', 'J. Azumah-Mensah',
    'Juliana Azumah Mensah', 'Azumah Mensah', 'Juliana Azumah',
    'Azumahmensah', 'Azumah-Mensah Gender', 'J. Azumah Mensah',
    'Azumah-Mensa', 'Juliana Mensah', 'Azumah-Mensah Juliana',
  ] },
  { canonical: 'Nana Oye Lithur', role: 'Min. of Gender (2013–2017)', aliases: [
    'Oye Lithur', 'Nana Oye Lithur', 'Lithur', 'N. Oye Lithur',
    'Nana Oye Litor', 'Oye Litor', 'Nana Lithur', 'Lithur Gender',
    'Nana Oye Lithur Gender', 'N. Lithur', 'Lithur Nana', 'Lithor',
  ] },
  { canonical: 'Elvis Afriyie-Ankrah', role: 'Min. of Youth (2012–2013)', aliases: [
    'Elvis Afriyie-Ankrah', 'Afriyie-Ankrah', 'E. Afriyie-Ankrah',
    'Elvis Afriyie Ankrah', 'Afriyie Ankrah', 'Elvis Ankrah',
    'Afriyieankrah', 'Afriyie-Ankrah Youth', 'Elvis Afriyie',
    'E. Afriyie Ankrah', 'Afriyie-Ankra', 'Ankrah Elvis',
  ] },
];


// ---------------------------------------------------------------------------
// Fourth Republic — Akufo-Addo (NPP) Administration (2017–2025)
// ---------------------------------------------------------------------------

const AKUFO_ADDO_MINISTERS = [
  { canonical: 'Ken Ofori-Atta', role: 'Min. of Finance (2017–2023)', aliases: [
    'Ken Ofori-Atta', 'Ofori-Atta', 'K. Ofori-Atta', 'Ken Ofori Atta',
    'Oforiatta', 'Ken Ofori', 'Ofori-Ata', 'Ofori-Atta Finance',
    'Ken Oforiatta', 'K. Ofori Atta', 'Ofori-Atta Ken', 'Ken O.',
  ] },
  { canonical: 'Mohammed Amin Adam', role: 'Min. of Finance (2024–2025)', aliases: [
    'Amin Adam', 'Mohammed Amin Adam', 'M. Amin Adam', 'Dr Amin Adam',
    'Amin Adams', 'Dr. Amin Adam', 'Amin Adam Finance', 'Mohammed Amin',
    'Amin Adam Mohammed', 'M. Adam', 'Ameen Adam', 'Amin Adam M.',
  ] },
  { canonical: 'Shirley Ayorkor Botchwey', role: 'Min. of Foreign Affairs (2017–2025)', aliases: [
    'Shirley Ayorkor Botchwey', 'Ayorkor Botchwey', 'S. Ayorkor Botchwey',
    'Shirley Botchwey', 'Ayorkor Botchway', 'Shirley Ayorkor',
    'Botchwey Foreign', 'S. Botchwey', 'Ayorkor Botchwey Shirley',
    'Shirley Ayorkor Botchway', 'Ayorkor', 'Shirley A. Botchwey',
  ] },
  { canonical: 'Gloria Akuffo', role: 'Attorney General (2017–2021)', aliases: [
    'Gloria Akuffo', 'G. Akuffo', 'Akuffo', 'Gloria Akufo',
    'Akuffo AG', 'Gloria Akuffo AG', 'Akufo', 'Akuffo Gloria',
    'G. Akufo', 'Gloria A.', 'Akuffo G.', 'Akufoh',
  ] },
  { canonical: 'Godfred Yeboah Dame', role: 'Attorney General (2021–2025)', aliases: [
    'Godfred Dame', 'G. Dame', 'Dame', 'Godfred Yeboah Dame',
    'Dame AG', 'Yeboah Dame', 'Godfred Dame AG', 'Dame Godfred',
    'G. Yeboah Dame', 'Godfred D.', 'Dame G.', 'Godfrey Dame',
  ] },
  { canonical: 'Ambrose Dery', role: 'Min. of Interior (2017–2025)', aliases: [
    'Ambrose Dery', 'A. Dery', 'Dery', 'Ambrose Derry',
    'Dery Interior', 'Ambrose Dery Interior', 'Derry', 'Dery Ambrose',
    'A. Derry', 'Ambrose D.', 'Dery A.', 'Ambrose Derey',
  ] },
  { canonical: 'Dominic Nitiwul', role: 'Min. of Defence (2017–2025)', aliases: [
    'Dominic Nitiwul', 'D. Nitiwul', 'Nitiwul', 'Dominic Nitiwol',
    'Nitiwul Defence', 'Dominic Nitiwul Defence', 'Nitiwol', 'Nitiwul Dominic',
    'D. Nitiwol', 'Dominic N.', 'Nitiwul D.', 'Nitiwool',
  ] },
  { canonical: 'Matthew Opoku Prempeh', role: 'Min. of Education (2017–21) / Energy (2021–24)', aliases: [
    'Opoku Prempeh', 'Matthew Opoku Prempeh', 'M. Opoku Prempeh',
    'NAPO', 'Dr Opoku Prempeh', 'Dr. Opoku Prempeh', 'Napo',
    'Opoku Prempeh Education', 'Opoku Prempe', 'Matthew Opoku',
    'Prempeh', 'M. Opoku',
  ] },
  { canonical: 'Yaw Osei Adutwum', role: 'Min. of Education (2021–2025)', aliases: [
    'Osei Adutwum', 'Yaw Osei Adutwum', 'Y. Osei Adutwum', 'Dr Adutwum',
    'Adutwum', 'Dr. Adutwum', 'Osei Adutwum Education', 'Adutwum Education',
    'Yaw Adutwum', 'Y. Adutwum', 'Osei Adutwom', 'Adutwom',
  ] },
  { canonical: 'Kwaku Agyeman-Manu', role: 'Min. of Health (2017–2023)', aliases: [
    'Agyeman-Manu', 'Kwaku Agyeman-Manu', 'K. Agyeman-Manu',
    'Agyeman Manu', 'Agyemanmanu', 'Kwaku Agyeman Manu',
    'Agyeman-Manu Health', 'Kwaku Manu', 'K. Agyeman Manu',
    'Agyeman-Manu Kwaku', 'Manu Health', 'Agyeman-Manuh',
  ] },
  { canonical: 'Kwasi Amoako-Atta', role: 'Min. of Roads (2017–2025)', aliases: [
    'Amoako-Atta', 'Kwasi Amoako-Atta', 'K. Amoako-Atta', 'Amoako Atta',
    'Kwasi Amoako Atta', 'Amoakoatta', 'Amoako-Atta Roads',
    'Kwasi Amoako', 'K. Amoako Atta', 'Amoako-Ata', 'Amoako-Atta Kwasi', 'Atta Roads',
  ] },
  { canonical: 'Boakye Agyarko', role: 'Min. of Energy (2017–2018)', aliases: [
    'Boakye Agyarko', 'B. Agyarko', 'Agyarko', 'Boakye Agyako',
    'Agyarko Energy', 'Boakye Agyarko Energy', 'Agyako', 'Agyarko Boakye',
    'B. Agyako', 'Boakye A.', 'Agyarko B.', 'Agyarkor',
  ] },
  { canonical: 'John Peter Amewu', role: 'Min. of Energy (2018–2021)', aliases: [
    'John Peter Amewu', 'J.P. Amewu', 'Amewu', 'John Amewu',
    'Peter Amewu', 'Amewu Energy', 'JP Amewu', 'Amewu John',
    'J. Amewu', 'Amewu J.P.', 'John Peter Amewoo', 'Amewoo',
  ] },
  { canonical: 'Alan Kyerematen', role: 'Min. of Trade (2017–2023)', aliases: [
    'Alan Kyerematen', 'A. Kyerematen', 'Kyerematen', 'Alan Cash',
    'Kyerematin', 'Alan Kyerematin', 'Kyerematen Trade', 'Alan K.',
    'Kyerematen Alan', 'A. Kyerematin', 'Alan Kyeremanten', 'Kyeremanten',
  ] },
  { canonical: 'Kwaku Asomah-Cheremeh', role: 'Min. of Lands (2017–2021)', aliases: [
    'Asomah-Cheremeh', 'Kwaku Asomah-Cheremeh', 'K. Asomah-Cheremeh',
    'Asomah Cheremeh', 'Asomahcheremeh', 'Kwaku Asomah Cheremeh',
    'Asomah-Chereme', 'Asomah-Cheremeh Lands', 'Kwaku Cheremeh',
    'K. Asomah Cheremeh', 'Cheremeh', 'Asomah-Cheremeh Kwaku',
  ] },
  { canonical: 'Samuel Abu Jinapor', role: 'Min. of Lands (2021–2025)', aliases: [
    'Abu Jinapor', 'Samuel Abu Jinapor', 'S. Abu Jinapor', 'Jinapor',
    'Abu Jinapor Lands', 'Samuel Jinapor', 'Jinnapor', 'Abu Jinapor Samuel',
    'S. Jinapor', 'Abu Jinapoh', 'Jinapor Lands', 'Abu Jinapor S.',
  ] },
  { canonical: 'Mustapha Abdul-Hamid', role: 'Min. of Information (2017–2018)', aliases: [
    'Mustapha Abdul-Hamid', 'M. Abdul-Hamid', 'Abdul-Hamid', 'Mustapha Hamid',
    'Abdul Hamid', 'Mustapha Abdul Hamid', 'Abdulhamid', 'Hamid Information',
    'Dr Mustapha Hamid', 'Dr. Mustapha Hamid', 'M. Hamid', 'Mustapha H.',
  ] },
  { canonical: 'Kojo Oppong Nkrumah', role: 'Min. of Information (2018–2025)', aliases: [
    'Kojo Oppong Nkrumah', 'Oppong Nkrumah', 'K. Oppong Nkrumah',
    'Oppong-Nkrumah', 'Kojo Oppong', 'Nkrumah Information',
    'Oppong Nkrumah Information', 'Oppong Nkruma', 'K. Oppong',
    'Oppong Nkrumah Kojo', 'Kojo Nkrumah', 'Oppong Nkrumahr',
  ] },
  { canonical: 'Ursula Owusu-Ekuful', role: 'Min. of Communications (2017–2025)', aliases: [
    'Ursula Owusu-Ekuful', 'Owusu-Ekuful', 'U. Owusu-Ekuful',
    'Ursula Owusu Ekuful', 'Owusu Ekuful', 'Ursula Owusu',
    'Owusuekuful', 'Owusu-Ekuful Communications', 'Ursula Ekuful',
    'U. Owusu Ekuful', 'Owusu-Ekuful Ursula', 'Owusu-Ekufol',
  ] },
  { canonical: 'Samuel Atta Akyea', role: 'Min. of Works (2017–2021)', aliases: [
    'Atta Akyea', 'Samuel Atta Akyea', 'S. Atta Akyea', 'Akyea',
    'Atta Akyea Works', 'Samuel Akyea', 'Atta Akyia', 'Akyia',
    'Atta Akyea Samuel', 'S. Akyea', 'Atta Akyea S.', 'Ata Akyea',
  ] },
  { canonical: 'Francis Asenso-Boakye', role: 'Min. of Works (2021–2025)', aliases: [
    'Asenso-Boakye', 'Francis Asenso-Boakye', 'F. Asenso-Boakye',
    'Asenso Boakye', 'Francis Asenso Boakye', 'Asensoboakye',
    'Asenso-Boakye Works', 'Francis Boakye', 'F. Asenso Boakye',
    'Asenso-Boakye Francis', 'Asenso-Boakyi', 'Francis Asenso',
  ] },
  { canonical: 'Ignatius Baffuor Awuah', role: 'Min. of Employment (2017–2025)', aliases: [
    'Ignatius Baffuor Awuah', 'Baffuor Awuah', 'I. Baffuor Awuah',
    'Ignatius Awuah', 'Baffour Awuah', 'Baffuor Awua',
    'Awuah Employment', 'Ignatius Baffuor', 'I. Awuah',
    'Baffuor Awuah Ignatius', 'Baffuor Awuah Employment', 'Bafuor Awuah',
  ] },
  { canonical: 'Hajia Alima Mahama', role: 'Min. of Local Govt (2017–2021)', aliases: [
    'Alima Mahama', 'Hajia Alima Mahama', 'H. Alima Mahama', 'Alima',
    'Hajia Alima', 'Alima Mama', 'Alima Mahama Local Govt', 'Hajia Mahama',
    'Alima Mahama Hajia', 'A. Mahama', 'Alimah Mahama', 'Hajia Alimah',
  ] },
  { canonical: 'Dan Botwe', role: 'Min. of Local Govt (2021–2025)', aliases: [
    'Dan Botwe', 'D. Botwe', 'Botwe', 'Dan Botwi',
    'Botwe Local Govt', 'Dan Botwe Local', 'Botwi', 'Botwe Dan',
    'D. Botwi', 'Dan B.', 'Botwe D.', 'Dan Botweh',
  ] },
  { canonical: 'Owusu Afriyie Akoto', role: 'Min. of Food & Agric (2017–2023)', aliases: [
    'Owusu Afriyie Akoto', 'Dr Afriyie Akoto', 'Afriyie Akoto',
    'Dr. Afriyie Akoto', 'O. Afriyie Akoto', 'Owusu Akoto',
    'Afriyie Akoto Agriculture', 'Akoto Agriculture', 'Afriyie Akotoh',
    'Afriyie Akoto Owusu', 'Dr Akoto', 'Owusu Afriyie',
  ] },
  { canonical: 'Bryan Acheampong', role: 'Min. of Food & Agric (2023–2025)', aliases: [
    'Bryan Acheampong', 'B. Acheampong', 'Acheampong', 'Bryan Acheampong Agric',
    'Brian Acheampong', 'Acheampong Bryan', 'Bryan Acheampong Agriculture',
    'B. Acheampong Agric', 'Acheampong Food', 'Bryan A.', 'Acheampong B.', 'Acheampung',
  ] },
  { canonical: 'Catherine Afeku', role: 'Min. of Tourism (2017–2019)', aliases: [
    'Catherine Afeku', 'C. Afeku', 'Afeku', 'Catherine Afeku Tourism',
    'Afeku Tourism', 'Catherine Afeaku', 'Afeaku', 'Afeku Catherine',
    'C. Afeaku', 'Catherine A.', 'Afeku C.', 'Afekuh',
  ] },
  { canonical: 'Barbara Oteng-Gyasi', role: 'Min. of Tourism (2019–2021)', aliases: [
    'Barbara Oteng-Gyasi', 'Oteng-Gyasi', 'B. Oteng-Gyasi',
    'Barbara Oteng Gyasi', 'Oteng Gyasi', 'Otenggyasi',
    'Oteng-Gyasi Tourism', 'Barbara Gyasi', 'B. Oteng Gyasi',
    'Oteng-Gyasi Barbara', 'Oteng-Gyasie', 'Barbara Oteng',
  ] },
  { canonical: 'Mohammed Awal', role: 'Min. of Tourism (2021–2025)', aliases: [
    'Mohammed Awal', 'M. Awal', 'Awal', 'Dr Awal',
    'Dr. Awal', 'Mohammed Awal Tourism', 'Awal Tourism', 'Awal Mohammed',
    'Mohamed Awal', 'M. Awal Tourism', 'Mohammed Awwal', 'Dr Mohammed Awal',
  ] },
  { canonical: 'Otiko Afisa Djaba', role: 'Min. of Gender (2017–2018)', aliases: [
    'Otiko Afisa Djaba', 'Otiko Djaba', 'O. Djaba', 'Djaba',
    'Otiko Afisa', 'Djaba Gender', 'Otiko Djaba Gender', 'Afisa Djaba',
    'Djaba Otiko', 'O. Afisa Djaba', 'Otiko Jaba', 'Djabah',
  ] },
  { canonical: 'Cynthia Morrison', role: 'Min. of Gender (2018–2021)', aliases: [
    'Cynthia Morrison', 'C. Morrison', 'Morrison', 'Cynthia Morison',
    'Morrison Gender', 'Cynthia Morrison Gender', 'Morison', 'Morrison Cynthia',
    'C. Morison', 'Cynthia M.', 'Morrison C.', 'Cynthia Morisun',
  ] },
  { canonical: 'Sarah Adwoa Safo', role: 'Min. of Gender (2021–2023)', aliases: [
    'Sarah Adwoa Safo', 'Adwoa Safo', 'S. Adwoa Safo', 'Sarah Safo',
    'Adwoa Safo Gender', 'Sarah Adwoa', 'Safo Gender', 'Adwoa Safor',
    'Sarah Safo Gender', 'S. Safo', 'Adwoa Safo Sarah', 'Safo Sarah',
  ] },
  { canonical: 'Isaac Kwame Asiamah', role: 'Min. of Youth (2017–2021)', aliases: [
    'Isaac Asiamah', 'Kwame Asiamah', 'I. Asiamah', 'Asiamah',
    'Asiamah Youth', 'Isaac Kwame Asiamah', 'Asiamah Isaac',
    'Kwame Asiamah Youth', 'I. Kwame Asiamah', 'Asiama', 'Isaac Asiama', 'Asiamah I.',
  ] },
  { canonical: 'Mustapha Ussif', role: 'Min. of Youth (2021–2025)', aliases: [
    'Mustapha Ussif', 'M. Ussif', 'Ussif', 'Mustapha Usif',
    'Ussif Youth', 'Mustapha Ussif Youth', 'Usif', 'Ussif Mustapha',
    'M. Usif', 'Mustapha U.', 'Ussif M.', 'Mustapha Ussiff',
  ] },
  { canonical: 'Kwaku Ofori Asiamah', role: 'Min. of Transport (2017–2025)', aliases: [
    'Kwaku Ofori Asiamah', 'Ofori Asiamah', 'K. Ofori Asiamah',
    'Kwaku Ofori Asiama', 'Ofori Asiamah Transport', 'Asiamah Transport',
    'Kwaku Asiamah', 'K. Asiamah', 'Ofori Asiama', 'Ofori Asiamah Kwaku',
    'Asiamah Kwaku', 'Ofori Asiamah K.',
  ] },
  { canonical: 'Albert Kan-Dapaah', role: 'Min. of Nat. Security (2017–2025)', aliases: [
    'Kan-Dapaah', 'Albert Kan-Dapaah', 'A. Kan-Dapaah', 'Kan Dapaah',
    'Kandapaah', 'Albert Kan Dapaah', 'Kan-Dapaa', 'Kan-Dapaah Security',
    'Albert Kandapaah', 'Kan-Dapaah Nat Security', 'A. Kan Dapaah', 'Kan-Dapa',
  ] },
  { canonical: 'Elizabeth Afoley Quaye', role: 'Min. of Fisheries (2017–2021)', aliases: [
    'Afoley Quaye', 'Elizabeth Afoley Quaye', 'E. Afoley Quaye',
    'Madam Afoley', 'Afoley Quaye Fisheries', 'Elizabeth Afoley',
    'Afoley Quay', 'E. Afoley', 'Afoley Quaye Elizabeth', 'Quaye Fisheries',
    'Afoley', 'Afoley Quaye E.',
  ] },
  { canonical: 'Mavis Hawa Koomson', role: 'Min. of Fisheries (2021–2025)', aliases: [
    'Hawa Koomson', 'Mavis Hawa Koomson', 'M. Hawa Koomson', 'Koomson',
    'Hawa Koomson Fisheries', 'Mavis Koomson', 'Hawa Komson',
    'Koomson Fisheries', 'Hawa Koomson Mavis', 'M. Koomson', 'Komson', 'Hawa Koomson M.',
  ] },
  { canonical: 'Kwabena Frimpong-Boateng', role: 'Min. of Environment (2017–2021)', aliases: [
    'Frimpong-Boateng', 'Kwabena Frimpong-Boateng', 'K. Frimpong-Boateng',
    'Prof Frimpong-Boateng', 'Frimpong Boateng', 'Frimpongboateng',
    'Prof. Frimpong-Boateng', 'Frimpong-Boateng Environment',
    'Kwabena Frimpong', 'K. Frimpong Boateng', 'Frimpong-Boateng Kwabena', 'Frimpong-Boating',
  ] },
  { canonical: 'Joe Ghartey', role: 'Min. of Railways (2017–2021)', aliases: [
    'Joe Ghartey', 'J. Ghartey', 'Ghartey', 'Joe Gartey',
    'Ghartey Railways', 'Joe Ghartey Railways', 'Ghartey Joe', 'J. Gartey',
    'Joseph Ghartey', 'Ghartei', 'Joe Ghartei', 'Ghartey Railways Min',
  ] },
  { canonical: 'Kofi Dzamesi', role: 'Min. of Chieftaincy (2017–2025)', aliases: [
    'Kofi Dzamesi', 'K. Dzamesi', 'Dzamesi', 'Kofi Dzamezi',
    'Dzamesi Chieftaincy', 'Kofi Dzamesi Chieftaincy', 'Dzamezi',
    'Dzamesi Kofi', 'K. Dzamezi', 'Kofi D.', 'Dzamesi K.', 'Dzamesie',
  ] },
  { canonical: 'Osei Kyei-Mensah-Bonsu', role: 'Majority Leader (2017–2025)', aliases: [
    'Kyei-Mensah-Bonsu', 'Osei Kyei-Mensah-Bonsu', 'O. Kyei-Mensah-Bonsu',
    'Kyei-Mensah Bonsu', 'Osei Kyei Mensah Bonsu', 'Kyeimensahbonsu',
    'Majority Leader', 'Kyei-Mensah-Bonsu Majority', 'Osei Kyei',
    'Kyei-Mensah-Bonsu Osei', 'K.M. Bonsu', 'Kyei-Mensah',
  ] },
  { canonical: 'Haruna Iddrisu', role: 'Minority Leader (2017–2021)', aliases: [
    'Haruna Iddrisu', 'H. Iddrisu', 'Iddrisu', 'Haruna Idrisu',
    'Iddrisu Minority', 'Haruna Iddrisu Minority', 'Idrisu',
    'Iddrisu Haruna', 'H. Idrisu', 'Haruna I.', 'Iddrisu H.', 'Harouna Iddrisu',
  ] },
  { canonical: 'Cassiel Ato Forson', role: 'Minority Leader (2023–2025)', aliases: [
    'Cassiel Ato Forson', 'Ato Forson', 'C. Ato Forson', 'Dr Ato Forson',
    'Cassiel Forson', 'Dr. Ato Forson', 'Forson', 'Ato Forson Minority',
    'Cassiel Ato', 'C. Forson', 'Ato Forson Cassiel', 'Ato Forsan',
  ] },
];


// ---------------------------------------------------------------------------
// Fourth Republic — Mahama (NDC) Administration (2025–)
// ---------------------------------------------------------------------------

const MAHAMA_2025_MINISTERS = [
  { canonical: 'Samuel Nartey George', role: 'Min. of Communications (2025–)', aliases: [
    'Sam George', 'Samuel Nartey George', 'S. Nartey George', 'Sam Nartey George',
    'Nartey George', 'Sam George Communications', 'Samuel George',
    'S. George', 'Nartey George Communications', 'Sam G.', 'George Sam', 'Sam Gorge',
  ] },
  { canonical: 'Cassiel Ato Forson', role: 'Min. of Finance/Defence (2025–)', aliases: [
    'Cassiel Ato Forson', 'Ato Forson', 'C. Ato Forson', 'Dr Ato Forson',
    'Cassiel Forson', 'Dr. Ato Forson', 'Forson', 'Ato Forson Finance',
    'Cassiel Ato', 'C. Forson', 'Ato Forson Defence', 'Ato Forsan',
  ] },
  { canonical: 'Haruna Iddrisu', role: 'Min. of Education (2025–)', aliases: [
    'Haruna Iddrisu', 'H. Iddrisu', 'Iddrisu', 'Haruna Idrisu',
    'Iddrisu Education', 'Haruna Iddrisu Education', 'Idrisu',
    'Iddrisu Haruna', 'H. Idrisu', 'Haruna I.', 'Iddrisu H.', 'Harouna Iddrisu',
  ] },
  { canonical: 'John Abdulai Jinapor', role: 'Min. of Energy (2025–)', aliases: [
    'John Jinapor', 'Abdulai Jinapor', 'J. Jinapor', 'Jinapor',
    'John Abdulai Jinapor', 'Jinapor Energy', 'Jinnapor', 'Abdulai Jinapor Energy',
    'J. Abdulai Jinapor', 'Jinapoh', 'John Jinapor Energy', 'Jinapor J.',
  ] },
  { canonical: 'Ibrahim Murtala Mohammed', role: 'Min. of Environment (2025–)', aliases: [
    'Murtala Mohammed', 'Ibrahim Murtala Mohammed', 'I. Murtala Mohammed',
    'Murtala', 'Ibrahim Murtala', 'Murtala Mohammed Environment',
    'Murtala Mohamed', 'Ibrahim Murtala Mohamed', 'Murtala M.',
    'I. Murtala', 'Murtala Mohammed Ibrahim', 'Mortala Mohammed',
  ] },
  { canonical: 'Emelia Arthur', role: 'Min. of Fisheries (2025–)', aliases: [
    'Emelia Arthur', 'E. Arthur', 'Arthur', 'Emilia Arthur',
    'Arthur Fisheries', 'Emelia Arthur Fisheries', 'Emelia Artur',
    'Arthur Emelia', 'E. Artur', 'Emelia A.', 'Arthur E.', 'Emelia Arther',
  ] },
  { canonical: 'Eric Opoku', role: 'Min. of Food & Agriculture (2025–)', aliases: [
    'Eric Opoku', 'E. Opoku', 'Opoku', 'Eric Opoku Agriculture',
    'Opoku Agriculture', 'Eric Opoku Food', 'Opoku Food', 'Opoku Eric',
    'E. Opoku Agriculture', 'Eric O.', 'Opoku E.', 'Eric Opokuh',
  ] },
  { canonical: 'Samuel Okudzeto Ablakwa', role: 'Min. of Foreign Affairs (2025–)', aliases: [
    'Okudzeto Ablakwa', 'Samuel Okudzeto Ablakwa', 'S. Okudzeto Ablakwa',
    'Ablakwa', 'Samuel Ablakwa', 'Okudzeto', 'Ablakwa Foreign',
    'Okudzeto Ablakwa Foreign', 'Ablakwa Samuel', 'S. Ablakwa',
    'Okudzeto Ablakwa S.', 'Ablakwah',
  ] },
  { canonical: 'Agnes Naa Momo Lartey', role: 'Min. of Gender (2025–)', aliases: [
    'Naa Momo Lartey', 'Agnes Naa Momo Lartey', 'A. Lartey', 'Lartey',
    'Agnes Lartey', 'Naa Momo', 'Lartey Gender', 'Agnes Naa Momo',
    'Naa Momo Lartey Gender', 'Momo Lartey', 'A. Naa Momo', 'Lartey Agnes',
  ] },
  { canonical: 'Kwabena Minta Akandoh', role: 'Min. of Health (2025–)', aliases: [
    'Minta Akandoh', 'Kwabena Minta Akandoh', 'K. Minta Akandoh', 'Akandoh',
    'Minta Akandoh Health', 'Kwabena Akandoh', 'Akandor', 'Akandoh Health',
    'K. Akandoh', 'Minta Akandor', 'Akandoh Minta', 'Minta Akandoh K.',
  ] },
  { canonical: 'Mohammed Mubarak Muntaka', role: 'Min. of Interior (2025–)', aliases: [
    'Mubarak Muntaka', 'Mohammed Mubarak Muntaka', 'M. Muntaka', 'Muntaka',
    'Mubarak Muntaka Interior', 'Mohammed Muntaka', 'Muntaka Interior',
    'Mubarak Muntaka Mohammed', 'M. Mubarak Muntaka', 'Muntaka M.', 'Muntakah', 'Mubarak M.',
  ] },
  { canonical: 'Dominic Ayine', role: 'Attorney General (2025–)', aliases: [
    'Dominic Ayine', 'D. Ayine', 'Ayine', 'Dr Ayine',
    'Dr. Ayine', 'Dominic Ayine AG', 'Ayine AG', 'Ayine Dominic',
    'D. Ayine AG', 'Dominic A.', 'Ayine D.', 'Dominic Ayini',
  ] },
  { canonical: 'Abdul-Rashid Pelpuo', role: 'Min. of Labor (2025–)', aliases: [
    'Abdul-Rashid Pelpuo', 'Pelpuo', 'A. Pelpuo', 'Rashid Pelpuo',
    'Pelpuo Labor', 'Abdul Rashid Pelpuo', 'Pelpuoh', 'Pelpuo Rashid',
    'A. Rashid Pelpuo', 'Pelpuo A.', 'Abdul-Rashid P.', 'Pelpuo Labour',
  ] },
  { canonical: 'Emmanuel Armah-Kofi Buah', role: 'Min. of Lands (2025–)', aliases: [
    'Armah-Kofi Buah', 'Emmanuel Buah', 'E. Buah', 'Buah',
    'Armah Kofi Buah', 'Emmanuel Armah-Kofi Buah', 'Buah Lands',
    'Armah-Kofi', 'Buah Emmanuel', 'E. Armah-Kofi Buah', 'Armah Kofi', 'Buahr',
  ] },
  { canonical: 'Ahmed Ibrahim', role: 'Min. of Local Government (2025–)', aliases: [
    'Ahmed Ibrahim', 'A. Ibrahim', 'Ibrahim', 'Ahmed Ibraheem',
    'Ibrahim Local Govt', 'Ahmed Ibrahim Local', 'Ibraheem', 'Ibrahim Ahmed',
    'A. Ibraheem', 'Ahmed I.', 'Ibrahim A.', 'Ahmed Ibrahem',
  ] },
  { canonical: 'Governs Kwame Agbodza', role: 'Min. of Roads (2025–)', aliases: [
    'Governs Agbodza', 'Kwame Agbodza', 'G. Agbodza', 'Agbodza',
    'Governs Kwame Agbodza', 'Agbodza Roads', 'Agbodzah', 'Agbodza Governs',
    'G. Kwame Agbodza', 'Governs A.', 'Agbodza G.', 'Governs Agbodzah',
  ] },
  { canonical: 'Abla Dzifa Gomashie', role: 'Min. of Tourism (2025–)', aliases: [
    'Dzifa Gomashie', 'Abla Dzifa Gomashie', 'A. Dzifa Gomashie', 'Gomashie',
    'Dzifa Gomashie Tourism', 'Abla Gomashie', 'Gomashi', 'Gomashie Tourism',
    'Dzifa Gomashie Abla', 'A. Gomashie', 'Gomashie Dzifa', 'Gomashieh',
  ] },
  { canonical: 'Elizabeth Ofosu Agyare', role: 'Min. of Trade (2025–)', aliases: [
    'Ofosu Agyare', 'Elizabeth Ofosu Agyare', 'E. Ofosu Agyare',
    'Ofosu-Agyare', 'Elizabeth Ofosu-Agyare', 'Ofosuagyare',
    'Ofosu Agyare Trade', 'Elizabeth Agyare', 'E. Ofosu-Agyare',
    'Ofosu Agyare Elizabeth', 'Ofosu Agyareh', 'Agyare Trade',
  ] },
  { canonical: 'Joseph Bukari Nikpe', role: 'Min. of Transportation (2025–)', aliases: [
    'Bukari Nikpe', 'Joseph Bukari Nikpe', 'J. Bukari Nikpe', 'Nikpe',
    'Bukari Nikpe Transportation', 'Joseph Nikpe', 'Nikpeh', 'Nikpe Transport',
    'J. Nikpe', 'Bukari Nikpe Joseph', 'Nikpe J.', 'Bukari Nikpe Transport',
  ] },
  { canonical: 'Kenneth Gilbert Adjei', role: 'Min. of Works & Housing (2025–)', aliases: [
    'Kenneth Adjei', 'Kenneth Gilbert Adjei', 'K. Adjei', 'Gilbert Adjei',
    'Kenneth Adjei Works', 'Adjei Works', 'K. Gilbert Adjei', 'Adjei Kenneth',
    'Kenneth G. Adjei', 'Adjei K.', 'Kenneth Adjey', 'Gilbert Adjey',
  ] },
  { canonical: 'George Opare Addo', role: 'Min. of Youth (2025–)', aliases: [
    'Opare Addo', 'George Opare Addo', 'G. Opare Addo', 'Pablo',
    'Opare Addo Youth', 'George Opare', 'Opare Ado', 'George Pablo',
    'G. Opare', 'Opare Addo George', 'Opare Addo G.', 'Opare Ado Youth',
  ] },
  { canonical: 'Johnson Asiama', role: 'Bank of Ghana Governor (2025–)', aliases: [
    'Johnson Asiama', 'J. Asiama', 'Asiama', 'Dr Asiama',
    'Dr. Asiama', 'Johnson Asiama BoG', 'Asiama BoG', 'Asiama Johnson',
    'Dr Johnson Asiama', 'J. Asiama BoG', 'Asiama Governor', 'Assiama',
  ] },
];


// ---------------------------------------------------------------------------
// Combined export
// ---------------------------------------------------------------------------

const ALL_MINISTERS = [
  ...FIRST_REPUBLIC_MINISTERS,
  ...SECOND_REPUBLIC_MINISTERS,
  ...THIRD_REPUBLIC_MINISTERS,
  ...PNDC_MINISTERS,
  ...RAWLINGS_MINISTERS,
  ...KUFUOR_MINISTERS,
  ...MILLS_MAHAMA_MINISTERS,
  ...AKUFO_ADDO_MINISTERS,
  ...MAHAMA_2025_MINISTERS,
];

module.exports = {
  FIRST_REPUBLIC_MINISTERS,
  SECOND_REPUBLIC_MINISTERS,
  THIRD_REPUBLIC_MINISTERS,
  PNDC_MINISTERS,
  RAWLINGS_MINISTERS,
  KUFUOR_MINISTERS,
  MILLS_MAHAMA_MINISTERS,
  AKUFO_ADDO_MINISTERS,
  MAHAMA_2025_MINISTERS,
  ALL_MINISTERS,
};
