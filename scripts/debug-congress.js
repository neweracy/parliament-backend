const { correctLocations } = require('../lib/location-correction');

// Simulate the exact server logic for "national democratic congress" as 3 raw words
const rawWords = [
  { word: 'the', start: 0.9, end: 1.0 },
  { word: 'national', start: 1.0, end: 1.2 },
  { word: 'democratic', start: 1.2, end: 1.4 },
  { word: 'congress', start: 1.4, end: 1.6 },
  { word: 'when', start: 1.6, end: 1.7 },
];

const wordStopwords = new Set(['a','an','the','in','on','at','to','of','is','are','was','were',
  'be','and','or','but','for','by','with','from','this','that','it','he','she','they','we',
  'his','her','their','our','my','your','as','so','if','not','through','has','had','have',
  'constituency','traditional','area','alongside','among','these','those','region',
  'district','municipal','metropolitan','assembly','parliament','bill','motion',
  'committee','minister','speaker','members','honorable','distinguished']);

const words = [];
for (let i = 0; i < rawWords.length; i++) {
  const w = rawWords[i];
  if (wordStopwords.has(w.word?.toLowerCase())) {
    words.push(w);
    continue;
  }

  if (w.word && w.word.length >= 4) {
    const singleResult = correctLocations(w.word);
    console.log(`single("${w.word}") => corrections:`, singleResult.corrections.length, 'text:', singleResult.text);
    if (singleResult.corrections.length > 0 && singleResult.corrections[0].confidence >= 0.90) {
      const corrText = singleResult.text;
      if (corrText.split(/\s+/).length <= 2) {
        words.push({ ...w, word: corrText, locationCorrected: true });
        continue;
      }
    }
  }

  if (i + 1 < rawWords.length) {
    const next = rawWords[i + 1];
    if (!wordStopwords.has(next.word?.toLowerCase()) && next.word) {
      const pair = w.word + ' ' + next.word;
      const pairResult = correctLocations(pair);
      console.log(`pair("${pair}") => corrections:`, pairResult.corrections.length, 'text:', pairResult.text);
      if (pairResult.corrections.length > 0 && pairResult.corrections[0].confidence >= 0.90) {
        words.push({ ...w, word: pairResult.text, end: next.end, locationCorrected: true });
        i++;
        continue;
      }
    }
  }

  words.push(w);
}

console.log('\nFinal:', words.map(w => w.word).join(' | '));
