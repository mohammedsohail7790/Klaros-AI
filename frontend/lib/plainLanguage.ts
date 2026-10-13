/**
 * Backend status text is written for engineers in places ("adapter", "module", "integration").
 * Everything shown to a business owner goes through here so the product speaks plainly.
 */
const REPLACEMENTS: [RegExp, string][] = [
  [/Integration adapter required/gi, "Not available yet"],
  [/Klaros has no module or integration for this yet/gi, "Klaros can't do this yet"],
  [/\bNeeds an adapter\b/gi, "Not available yet"],
  [/\badapter\b/gi, "connection"],
];

export function plainLanguage(text: string): string {
  return REPLACEMENTS.reduce((acc, [pattern, to]) => acc.replace(pattern, to), text);
}
