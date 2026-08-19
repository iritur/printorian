/**
 * The Russian plural of a noun, by its count.
 *
 * Three forms, not two: 1 модель, 4 модели, 11 моделей. English gets away with a
 * singular test and Russian does not, and «4 готовых моделей» on a landing page
 * is the kind of thing that makes a product look machine-translated — on the one
 * screen whose whole argument is that its details are checked.
 *
 * Here rather than in a screen's own module because it is a property of the
 * language, and two screens already need it.
 */
export function plural(count: number, one: string, few: string, many: string): string {
  // The teens are the trap: 11 and 14 take the many form despite ending in 1
  // and 4, so they are excluded before the last digit is looked at.
  const mod100 = count % 100
  if (mod100 >= 11 && mod100 <= 14) return many

  const mod10 = count % 10
  if (mod10 === 1) return one
  if (mod10 >= 2 && mod10 <= 4) return few
  return many
}
