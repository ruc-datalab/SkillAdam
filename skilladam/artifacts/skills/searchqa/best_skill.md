---
name: concise-canonical-qa-answering
description: Choose concise canonical answer forms for clue-style question answering.
when_to_use:
  - Answering clue-style questions where the final output should be only the short canonical answer.
---

# Concise Canonical QA Answering

## Workflow

- **Resolve the clue target**: Determine what type of answer the wording asks for, such as a person, place, title, category, object, or term. For quotation-only or dialogue clues, identify the implied source, speaker, author, character, or quoted entity rather than continuing the quote unless completion is explicitly requested. For ellipsis or partial phrase/title clues, supply the omitted identifying word or words rather than repeating the visible fragment. Use surrounding descriptors to choose the intended entity, not merely an associated fact.
- **Choose the canonical level of specificity**: Prefer the shortest standard answer that satisfies the clue. If the clue points to a broad institution, category, familiar name, or title, do not substitute a more specific branch, full legal name, subtitle, generic head noun copied from the clue, or explanatory phrase.
- **Match conventional answer form**: Preserve established title styling, common-name usage, possessive relationship wording, singular/plural or mass-noun form, and idiomatic terminology when they are part of the expected answer.

## Error Avoidance

- Use conventional personal-name and title length; do not over-expand names or omit standard honorifics from common answer forms.
  - '(Jimmy) Doolittle' not 'James “Jimmy” Doolittle'
  - 'Cassius Clay' not 'Cassius Marcellus Clay Jr.'
  - 'Wordsworth' not 'William Wordsworth'
  - 'Genet' not 'Jean Genet'
  - 'Clark' not 'Wesley Clark'
  - 'Lyndon Johnson' not 'Lyndon B. Johnson'
  - 'George Bush' not 'George H. W. Bush'
  - 'Lord Robert Baden-Powell' not 'Robert Baden-Powell'
  - 'Sir Francis Drake' not 'Francis Drake'
  - 'Sir Isaac Newton' not 'Isaac Newton'
  - 'Boss' not 'Boss Tweed'
  - 'Stuart Sutcliffe' not 'Stu Sutcliffe'
  - 'Patricia Hearst' not 'Patty Hearst'
  - 'Gibran' not 'Kahlil Gibran'
- Match the clue's canonical specificity; do not narrow, broaden, omit required official qualifiers, or strip required words from established compounds or phrases.
  - 'the University of Wisconsin' not 'University of Wisconsin–Superior'
  - 'Trains' not 'passenger trains'
  - 'Brown pelican' not 'pelican'
  - 'a eucalyptus tree' not 'eucalyptus'
  - 'bubble gum' not 'modern chewing gum'
  - 'a timing gun' not 'timing'
  - 'a balloon' not 'a hot air balloon'
  - 'laundries' not 'hand laundries'
  - 'The Polar Express' not 'Polar'
  - 'Forbes magazine' not 'Forbes'
  - 'The People's Republic of China' not 'China'
  - 'turning state's evidence' not 'state's evidence'
- Do not append a generic noun, inferred category label, or descriptive modifier when the expected response is the core term.
  - 'Pershing' not 'Pershing missile'
  - 'Pyre' not 'funeral pyre'
  - 'Doppler' not 'Doppler radar'
  - 'the Amazon' not 'Amazon River'
  - 'hydroelectric' not 'Hydroelectric power'
  - 'Bull Moose' not 'Bull Moose Party'
  - 'radical' not 'radical sign'
  - 'Roanoke' not 'Roanoke Island'
  - 'Coca-Cola' not 'The Coca-Cola Company'
  - 'C' not 'vitamin C'
  - 'Kenya' not 'Mount Kenya'
  - 'Grambling' not 'Grambling State University'
  - 'a thyroid' not 'thyroid gland'
- Check conventional spelling, spacing, abbreviation form, grammatical inflection, and singular/plural form; avoid pluralizing mass beverages, materials, or category names unnecessarily.
  - 'Showboat' not 'Show Boat'
  - 'Mt. Wilson' not 'Mount Wilson'
  - 'Vaclav Havel' not 'Václav Havel'
  - 'W.C. Fields' not 'W. C. Fields'
  - 'Grauman's Chinese Theater' not 'Grauman's Chinese Theatre'
  - 'Fungi' not 'Fungus'
  - 'tsunami' not 'Tsunamis'
  - 'a torpedo' not 'torpedoes'
  - 'a falcon' not 'falcons'
  - 'Well' not 'wells'
  - 'Dalai Lama' not 'Dalai Lamas'
  - 'Meandering' not 'meander'
- Avoid defaulting to a relationship, shared category, or descriptive definition when the clue asks for an associated entity or specific listed item.
  - 'Monaco' not 'Sisters'
  - 'Brunei' not 'Sultan of Brunei'
  - 'Massachusetts' not 'U.S. Senators from Massachusetts'
  - 'Sweden' not 'Nordic countries'
  - 'Oklahoma' not 'a 146-acre Oklahoma state park in Cherokee County'
  - 'Australia' not 'Australia's highest peak'
  - 'Italy' not 'Italian firearms manufacturer'
  - 'Germany' not 'a city in Germany'
  - 'Priscilla Presley' not 'American singer and songwriter'