/**
 * Ghana Members of Parliament Dataset
 *
 * Current 9th Parliament (2025–2029) and notable members from the
 * 8th Parliament (2021–2025). Used for post-processing correction
 * of ASR output — recognizes MPs by surname, initials, honorific
 * prefix ("Honorable"), and common ASR misspellings.
 *
 * Source: parliament.gh (fetched July 2026)
 *
 * Format: { name, constituency, party, aliases? }
 * Aliases: surname-only, initials, and common ASR errors.
 */

'use strict';

// ---------------------------------------------------------------------------
// 9th Parliament (2025–2029) — Current MPs
// Sourced from parliament.gh/members (partial, key members)
// ---------------------------------------------------------------------------

const CURRENT_MPS = [
  // Leadership
  { name: 'Alban Sumana Kingsford Bagbin', constituency: 'Nadowli/Kaleo', party: 'NDC', aliases: ['Bagbin', 'Speaker Bagbin', 'Rt Hon Bagbin', 'Alban Bagbin'] },
  { name: 'Alexander Kwamena Afenyo-Markin', constituency: 'Effutu', party: 'NPP', aliases: ['Afenyo-Markin', 'Afenyo Markin'] },
  { name: 'Cassiel Ato Forson', constituency: 'Ajumako-Enyan-Essiam', party: 'NDC', aliases: ['Ato Forson', 'Dr Ato Forson', 'Cassiel Ato'] },
  { name: 'Haruna Iddrisu', constituency: 'Tamale South', party: 'NDC', aliases: ['Haruna', 'Haruna Iddrissu', 'Haruna Idrissu'] },

  // Notable current members (from parliament.gh fetch)
  { name: 'Abdul Rauf Tongym Tubazu', constituency: 'Ayawaso Central', party: 'NDC', aliases: ['Tubazu'] },
  { name: 'Abdul Aziz Fatahiya', constituency: 'Savelugu', party: 'NPP', aliases: ['Fatahiya'] },
  { name: 'Abdul Kabiru Tiah Mahama', constituency: 'Walewale', party: 'NPP', aliases: ['Tiah Mahama'] },
  { name: 'Abdul-Fatawu Alhassan', constituency: 'Yendi', party: 'NDC', aliases: ['Abdul-Fatawu'] },
  { name: 'Abdul-Khaliq Mohammed Sherif', constituency: 'Nanton', party: 'NDC', aliases: ['Sherif', 'Abdul-Khaliq'] },
  { name: 'Abdul-Rashid Hassan Pelpuo', constituency: 'Wa Central', party: 'NDC', aliases: ['Pelpuo', 'Dr Pelpuo'] },
  { name: 'Abdul-Salam Adams', constituency: 'New Edubiase', party: 'NDC', aliases: ['Abdul-Salam'] },
  { name: 'Abed-Nego Lamangin Bandim', constituency: 'Bunkpurugu', party: 'NDC', aliases: ['Bandim'] },
  { name: 'Abena Osei-Asare', constituency: 'Atiwa East', party: 'NPP', aliases: ['Osei-Asare', 'Abena Osei Asare'] },
  { name: 'Abla Dzifa Gomashie', constituency: 'Ketu South', party: 'NDC', aliases: ['Dzifa Gomashie', 'Gomashie'] },
  { name: 'Adama Sulemana', constituency: 'Tain', party: 'NDC', aliases: ['Adama'] },
  { name: 'Adelaide Ntim', constituency: 'Nsuta/Kwaman Beposo', party: 'NPP', aliases: ['Adelaide'] },
  { name: 'Agnes Naa Momo Lartey', constituency: 'Krowor', party: 'NDC', aliases: ['Naa Momo', 'Agnes Lartey'] },
  { name: 'Ahmed Ibrahim', constituency: 'Banda', party: 'NDC', aliases: ['Ahmed Ibrahim'] },
  { name: 'Akwasi Konadu', constituency: 'Manhyia North', party: 'NPP', aliases: ['Konadu'] },
  { name: 'Albert Tetteh Nyakotey', constituency: 'Yilo Krobo', party: 'NDC', aliases: ['Nyakotey'] },
  { name: 'Alexander Roosevelt Hottordze', constituency: 'Central Tongu', party: 'NDC', aliases: ['Hottordze'] },
  { name: 'Alexander Akwasi Acquah', constituency: 'Akim Oda', party: 'NPP', aliases: ['Acquah'] },
  { name: 'Alfred Okoe Vanderpuije', constituency: 'Ablekuma South', party: 'NDC', aliases: ['Vanderpuije', 'Okoe Vanderpuye'] },
  { name: 'Andrew Asiamah Amoako', constituency: 'Fomena', party: 'NPP', aliases: ['Asiamah Amoako'] },
  { name: 'Ayariga Mahama', constituency: 'Bawku Central', party: 'NDC', aliases: ['Ayariga'] },
  { name: 'Samuel Okudzeto Ablakwa', constituency: 'North Tongu', party: 'NDC', aliases: ['Ablakwa', 'Okudzeto Ablakwa', 'Okudzeto'] },
  { name: 'Samuel Nartey George', constituency: 'Ningo-Prampram', party: 'NDC', aliases: ['Sam George', 'Nartey George', 'Sam Nartey George'] },
  { name: 'John Abdulai Jinapor', constituency: 'Damongo', party: 'NDC', aliases: ['Jinapor', 'John Jinapor'] },
  { name: 'Ibrahim Murtala Mohammed', constituency: 'Tamale Central', party: 'NDC', aliases: ['Murtala Mohammed', 'Ibrahim Murtala'] },
  { name: 'Kwabena Minta Akandoh', constituency: 'Juaboso', party: 'NDC', aliases: ['Akandoh', 'Minta Akandoh'] },
  { name: 'Mohammed Mubarak Muntaka', constituency: 'Asawase', party: 'NDC', aliases: ['Muntaka', 'Mubarak Muntaka'] },
  { name: 'Dominic Ayine', constituency: 'Bolgatanga East', party: 'NDC', aliases: ['Dr Ayine', 'Dominic Akuritinga Ayine'] },
  { name: 'Emmanuel Armah-Kofi Buah', constituency: 'Ellembele', party: 'NDC', aliases: ['Armah-Kofi Buah', 'Emmanuel Buah'] },
  { name: 'Governs Kwame Agbodza', constituency: 'Adaklu', party: 'NDC', aliases: ['Agbodza', 'Governs Agbodza'] },
  { name: 'Eric Opoku', constituency: 'Asunafo South', party: 'NDC', aliases: ['Eric Opoku'] },
  { name: 'Elizabeth Ofosu Agyare', constituency: 'Akwatia', party: 'NDC', aliases: ['Ofosu Agyare'] },
  { name: 'Kojo Oppong Nkrumah', constituency: 'Ofoase Ayirebi', party: 'NPP', aliases: ['Oppong Nkrumah', 'Kojo Oppong'] },
  { name: 'Ursula Owusu-Ekuful', constituency: 'Ablekuma West', party: 'NPP', aliases: ['Ursula Owusu', 'Owusu-Ekuful'] },
  { name: 'Matthew Opoku Prempeh', constituency: 'Manhyia South', party: 'NPP', aliases: ['NAPO', 'Opoku Prempeh'] },
  { name: 'Osei Kyei-Mensah-Bonsu', constituency: 'Suame', party: 'NPP', aliases: ['Kyei-Mensah-Bonsu'] },
  { name: 'Dan Botwe', constituency: 'Okere', party: 'NPP', aliases: ['Dan Botwe', 'Dan Kwaku Botwe'] },
  { name: 'Ken Ofori-Atta', constituency: 'Abuakwa South', party: 'NPP', aliases: ['Ofori-Atta', 'Ken Ofori Atta'] },
  { name: 'Godfred Yeboah Dame', constituency: 'Abuakwa North', party: 'NPP', aliases: ['Godfred Dame'] },
  { name: 'Francis Asenso-Boakye', constituency: 'Bantama', party: 'NPP', aliases: ['Asenso-Boakye'] },
  { name: 'Joseph Osei-Owusu', constituency: 'Bekwai', party: 'NPP', aliases: ['Osei-Owusu', 'Joe Wise'] },
  { name: 'Kwaku Agyeman-Manu', constituency: 'Dormaa Central', party: 'NPP', aliases: ['Agyeman-Manu'] },
  { name: 'Dominic Nitiwul', constituency: 'Bimbilla', party: 'NPP', aliases: ['Nitiwul'] },
  { name: 'Alan Kyerematen', constituency: '', party: 'NPP', aliases: ['Kyerematen', 'Alan Cash', 'Alan Kyeremanteng'] },
  { name: 'Ignatius Baffuor Awuah', constituency: 'Sunyani West', party: 'NPP', aliases: ['Baffuor Awuah'] },
  { name: 'Kwasi Amoako-Atta', constituency: 'Atiwa West', party: 'NPP', aliases: ['Amoako-Atta', 'Amoako Attah'] },
  { name: 'Samuel Abu Jinapor', constituency: 'Damongo', party: 'NPP', aliases: ['Abu Jinapor'] },
  { name: 'Ambrose Dery', constituency: 'Nandom', party: 'NPP', aliases: ['Dery', 'Ambrose Derry'] },
  { name: 'Bede Anwataazumo Ziedeng', constituency: 'Lawra', party: 'NDC', aliases: ['Ziedeng'] },
  { name: 'Benjamin Narteh Ayiku', constituency: 'Ledzokuku', party: 'NDC', aliases: ['Ayiku'] },
  { name: 'Alhassan Tampuli Sulemana', constituency: 'Gushegu', party: 'NPP', aliases: ['Tampuli'] },
  { name: 'Andrew Dari Chiwitey', constituency: 'Sawla/Tuna/Kalba', party: 'NDC', aliases: ['Chiwitey'] },
  { name: 'Bawah Muhammad Braimah', constituency: 'Ejura Sekyeredumase', party: 'NDC', aliases: ['Braimah'] },
  { name: 'Anthony Mwinkaara Sumah', constituency: 'Nadowli/Kaleo', party: 'NDC', aliases: ['Sumah'] },
];

// ---------------------------------------------------------------------------
// 8th Parliament (2021–2025) — Notable previous MPs not in current parliament
// ---------------------------------------------------------------------------

const PREVIOUS_MPS = [
  { name: 'Patrick Yaw Boamah', constituency: 'Okaikwei Central', party: 'NPP', aliases: ['Boamah'] },
  { name: 'Bryan Acheampong', constituency: 'Abetifi', party: 'NPP', aliases: ['Bryan Acheampong'] },
  { name: 'Lydia Seyram Alhassan', constituency: 'Ayawaso West Wuogon', party: 'NPP', aliases: ['Lydia Alhassan'] },
  { name: 'Mavis Hawa Koomson', constituency: 'Awutu Senya East', party: 'NPP', aliases: ['Hawa Koomson'] },
  { name: 'Sarah Adwoa Safo', constituency: 'Dome Kwabenya', party: 'NPP', aliases: ['Adwoa Safo'] },
  { name: 'Henry Quartey', constituency: 'Ayawaso Central', party: 'NPP', aliases: ['Quartey'] },
  { name: 'Kwame Anyimadu-Antwi', constituency: 'Asante Akim Central', party: 'NPP', aliases: ['Anyimadu-Antwi'] },
  { name: 'Mark Assibey-Yeboah', constituency: 'New Juaben South', party: 'NPP', aliases: ['Assibey-Yeboah'] },
  { name: 'Ebenezer Kojo Kum', constituency: 'Ahanta West', party: 'NPP', aliases: ['Kojo Kum'] },
  { name: 'Kwabena Mintah Akandoh', constituency: 'Juaboso', party: 'NDC', aliases: ['Akandoh'] },
  { name: 'Yaw Buaben Asamoa', constituency: 'Adentan', party: 'NPP', aliases: ['Buaben Asamoa'] },
  { name: 'Emmanuel Kwasi Bedzrah', constituency: 'Ho West', party: 'NDC', aliases: ['Bedzrah'] },
  { name: 'Nii Lante Vanderpuije', constituency: 'Odododiodio', party: 'NDC', aliases: ['Nii Lante'] },
  { name: 'Rockson-Nelson Dafeamekpor', constituency: 'South Dayi', party: 'NDC', aliases: ['Dafeamekpor'] },
  { name: 'Mahama Ayariga', constituency: 'Bawku Central', party: 'NDC', aliases: ['Ayariga'] },
  { name: 'Clement Apaak', constituency: 'Builsa South', party: 'NDC', aliases: ['Apaak', 'Dr Apaak'] },
  { name: 'Isaac Adongo', constituency: 'Bolgatanga Central', party: 'NDC', aliases: ['Adongo'] },
  { name: 'Kofi Adams', constituency: 'Buem', party: 'NDC', aliases: ['Kofi Adams'] },
  { name: 'James Klutse Avedzi', constituency: 'Ketu North', party: 'NDC', aliases: ['Avedzi'] },
  { name: 'Ras Mubarak', constituency: 'Kumbungu', party: 'NDC', aliases: ['Ras Mubarak'] },
  { name: 'Inusah Fuseini', constituency: 'Tamale Central', party: 'NDC', aliases: ['Inusah Fuseini', 'Fusheini'] },
  { name: 'Alhassan Suhuyini', constituency: 'Tamale North', party: 'NDC', aliases: ['Suhuyini'] },
  { name: 'Peter Nortsu-Kotoe', constituency: 'Akatsi North', party: 'NDC', aliases: ['Nortsu-Kotoe'] },
  { name: 'Zanetor Agyeman-Rawlings', constituency: 'Klottey Korle', party: 'NDC', aliases: ['Zanetor', 'Agyeman-Rawlings'] },
  { name: 'Kwame Agbodza', constituency: 'Adaklu', party: 'NDC', aliases: ['Agbodza'] },
  { name: 'Fiifi Kwetey', constituency: 'Keta', party: 'NDC', aliases: ['Fiifi Kwetey', 'Kwetey'] },
];

// ---------------------------------------------------------------------------
// Combined and export
// ---------------------------------------------------------------------------

const ALL_MPS = [...CURRENT_MPS, ...PREVIOUS_MPS];

module.exports = {
  CURRENT_MPS,
  PREVIOUS_MPS,
  ALL_MPS,
};
