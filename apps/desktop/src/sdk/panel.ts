// Part of @hermes/plugin-sdk (re-exported by ./index), kept apart so the SDK entry stays
// within its size cap.

/** THE master-detail toolkit core uses for list+inspector surfaces (Scheduled
 *  jobs, Kanban, …): a dense left `PanelList` of `PanelListRow`s beside a
 *  scrolling `PanelDetail` of `PanelSectionLabel` / `PanelMeta` / `PanelBlock`.
 *  `PanelEmpty` is the icon+action empty state (plain `EmptyState` is title +
 *  description only, and silently drops an `icon`). A row takes a custom `lead`
 *  (avatar/swatch), trailing `meta`, and `menuItems` for kebab + right-click
 *  parity, so a roster needs no hand-rolled row. The overlay-bound `Panel` root
 *  is deliberately NOT exported — these compose inside a pane just as well. */
export {
  PanelAction,
  PanelAddButton,
  PanelBlock,
  PanelBody,
  PanelDetail,
  PanelEmpty,
  PanelHeader,
  PanelList,
  PanelListRow,
  type PanelMenuItem,
  PanelMeta,
  type PanelMetaRow,
  PanelPill,
  type PanelPillTone,
  PanelRowMenu,
  PanelSectionLabel
} from '@/app/overlays/panel'
