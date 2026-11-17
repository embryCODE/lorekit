# Alien Evolved local reference content

Alien support is experimental and passed independent acceptance review for the
scoped cinematic loop on 2026-11-17. The resolver identifies itself as
`evolved-beta2`; deferred subsystems and final-edition reconciliation remain
outside this profile.

Content is imported through `alien_content_import` before creating actors. The
source PDF and its extracted text stay outside git. Bundles are canonical JSON,
written atomically under `LOREKIT_ALIEN_CONTENT_DIR` (default: `alien-content`
beside the session database), and addressed by their SHA-256 digest. A checkpoint
records the digest, not a substitute catalog. Restore the matching local bundle
when moving saves to another machine. The backend rejects a missing or changed
bundle instead of silently replacing it.

## Envelope

```json
{
  "profile": "evolved-beta2",
  "source": "Your local reference title/version and source fingerprint",
  "collections": {
    "careers": {}, "talents": {}, "items": {},
    "creatures": {}, "injuries": {}, "traumas": {}
  }
}
```

Collection keys are stable lower-case IDs using letters, numbers, `_` or `-`.
Every record has `page`, a positive printed source-page number. Fields are
mechanical data; a bundle is not a place for a copied rulebook corpus. Unknown
item properties, hooks, nested signature operations and missing references must
be resolved before import. Schema validation is not an audit of the supplied
numbers against your book; results carry the bundle fingerprint for that reason.

## Collections

- Careers declare `attribute`, three key `skills`, and selectable `talents` IDs.
  Standard creation checks the 14-point/10-point allocation, career caps and one
  starting career talent. Imports validate final values without imposing a
  new-character budget.
- Talents declare implemented `effects`, each with a supported `hook` and its
  parameters. Effects are not arbitrary Python expressions. Empty/inert talents
  and unsupported hooks reject rather than granting fictional automation.
- Items declare `weight`, optional `capacity` and `supply_type` (`air`, `ammo`,
  `power`). Weapon fields include integer `damage`, `modifier`, `minimum` and
  `maximum` range bands (Adjacent=0 through Extreme=4), and supported boolean
  features. `check_modifiers` are `{skill,context,value}` records; `single_use`
  consumes one inventory quantity when the check is rolled. Armor/protection
  fields are `armor`, `acid_proof`, `facehug_proof` and `vacuum_proof`.
  Consumables declare `consume` using a supported effect (`wake_day`,
  `hypersleep_relief`, `stress_zero`, `coffee`, `alcohol`, `food`, `water`,
  `nutrition`, `meal_relief`, `recreation_up` or `recreation_down`), plus optional
  `dose_group`. The resolver records quantity, per-shift doses, overdose penalties
  and expiry. `alien_combat_act` accepts the `consume` action with the inventory
  instance and a GM-selected quick/full cost. Unknown drug effects reject.
- Creatures declare `health`, `speed`, skill-only `skills`, optional `armor`,
  `armor_fire`, `acid`, `stealth_penalty`, `early_stage`, and six `attacks`
  indexed by strings `"1"` through `"6"`. Each signature has a stable `id` and
  an `actions` list. Shared IDs represent repeated faces of the same signature.
  An egg has `stage:"egg"`, Speed zero and a local `spawn` reference instead of
  six signature attacks. `birth`/`matures` reference local life-cycle templates.
- Injuries use D66 keys and declare effects, `lethal`, optional `interval`
  (`round`, `stretch`, `shift`), `instant_death`, `surgery`, `prosthesis` and
  `healing:{unit,amount}`. Healing units are `shift` or `day`; amount is a
  positive integer or `1d6` through `4d6`. Damage requires all 36 D66 outcomes.
- Traumas use all 36 D66 keys and supported mechanical effect hooks. Importing
  the table does not opt a group into trauma. Enabling `mental_trauma` is a
  separate explicit session option and requires a complete table.

An inventory entry has a unique instance `id`, `content_id`, `quantity`,
`equipped`, `supply` and `reloads`. `active_air:true` explicitly declares that the
character is relying on that equipped air source; spare/unused tanks do not
silently roll every stretch. Corroded worn armor is stored on its inventory
instance as `armor_current`, so taking it off does not repair it.

## Initial roster boundary

The first cinematic roster to verify is human and synthetic NPCs plus Ovomorph,
Facehugger, Chestburster and adult Drone. Use local IDs `ovomorph`, `facehugger`,
`chestburster`, `drone` for that reference bundle. Egg release must reference the
Facehugger; infection/birth and maturation require the Chestburster/adult
references. All reachable signatures and special abilities need implementation
and tests before those templates are advertised as fully playable.

Other species, vehicle and ship catalogs, first-edition conversion, random/life
path creation, campaign advancement and Last Survivor remain outside the initial
release. An explicit GM ruling is recorded through `alien_rules_override`; it is
not reported as an automated application of an unsupported mechanic.

## Visibility and writes

Mutation tools are GM-only and label their results accordingly. Use status/view
with `audience:"public"` for narration facts. Generic character attribute,
inventory and ability writers cannot alter Alien mechanics; use Alien tools.
Raw exports are labeled GM-only and can contain agendas and hidden state.
