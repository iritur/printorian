import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { api } from '@printorian/ui'

import { bytes, shortDate } from './account'
import type { ModelAsset, Shelf } from './account'

/**
 * «Мои модели» — the geometry this customer has uploaded.
 *
 * Two facts on each card that the kit shows and the farm can actually measure:
 * the bounding box, and whether the mesh is watertight. The second is the one
 * that matters — a mesh with holes has no defined volume, so it has no price,
 * and the card says «ЦЕНА НЕ ОПРЕДЕЛЕНА» rather than a figure derived from a
 * guess at what the inside of it looks like.
 *
 * The kit also prints a print time and a price per card. Neither is on the card
 * here: both depend on material, quantity and finish, which are choices made in
 * the configurator, and a number that changes the moment you press the button
 * beside it is worse than no number.
 */
export function AccountModels({
  locale,
  onOrder,
  onUpload,
}: {
  locale: Locale
  onOrder: (asset: ModelAsset) => void
  onUpload: () => void
}) {
  const [shelf, setShelf] = useState<Shelf | null>(null)

  useEffect(() => {
    void api
      .get<Shelf>('/account/models')
      .then(setShelf)
      .catch(() => setShelf({ models: [], used_bytes: 0, quota_bytes: 0 }))
  }, [])

  if (!shelf) return <p className="hv-hint">Загрузка…</p>

  return (
    <>
      <div className="hv-row hv-row--between">
        <span className="hv-micro">ЗАГРУЖЕННЫЕ ВАМИ ФАЙЛЫ · ХРАНЯТСЯ, ПОКА ИМИ ПОЛЬЗУЮТСЯ</span>
        <button className="hv-btn hv-btn--sm hv-btn--primary" type="button" onClick={onUpload}>
          Загрузить модель
        </button>
      </div>

      {shelf.models.length === 0 ? (
        <p className="hv-hint">
          Здесь появятся файлы, которые вы загрузите в конфигуратор. Один и тот же файл
          хранится один раз, сколько бы раз его ни отправляли.
        </p>
      ) : (
        <div className="hv-cat">
          {shelf.models.map(({ asset, orders }) => (
            <Card
              key={asset.id}
              locale={locale}
              asset={asset}
              orders={orders}
              onOrder={() => onOrder(asset)}
            />
          ))}
        </div>
      )}

      <div className="hv-panel__foot" style={{ border: '1px solid var(--hv-line)' }}>
        <span>
          ЗАНЯТО {bytes(shelf.used_bytes, locale)} ИЗ {bytes(shelf.quota_bytes, locale)}
        </span>
        <span>ФАЙЛЫ НЕ ПЕРЕДАЮТСЯ ТРЕТЬИМ ЛИЦАМ</span>
      </div>
    </>
  )
}

function Card({
  locale,
  asset,
  orders,
  onOrder,
}: {
  locale: Locale
  asset: ModelAsset
  orders: number
  onOrder: () => void
}) {
  const size = [asset.width_mm, asset.depth_mm, asset.height_mm]
    .map((value) => Math.round(Number(value)))
    .join(' × ')

  return (
    <article className="hv-frame hv-model" style={asset.is_watertight ? undefined : { opacity: 0.55 }}>
      <div className="hv-model__view">
        <span className={`hv-model__tag hv-model__tag--tl${asset.is_watertight ? '' : ' hv-warn'}`}>
          {asset.is_watertight ? asset.format.toUpperCase() : 'НЕ ГЕРМЕТИЧНА'}
        </span>
        <Wireframe asset={asset} />
        <span className="hv-model__tag hv-model__tag--br">{bytes(asset.size_bytes, locale)}</span>
      </div>
      <div className="hv-model__body">
        <h2 className="hv-model__title">{asset.original_filename.toUpperCase()}</h2>
        <div className="hv-model__meta">
          <span>{asset.is_watertight ? `${size} ММ` : 'ЦЕНА НЕ ОПРЕДЕЛЕНА'}</span>
          <span>{orders > 0 ? `ЗАКАЗОВ ${orders}` : 'НЕ ЗАКАЗЫВАЛАСЬ'}</span>
        </div>
      </div>
      <div className="hv-model__foot">
        <span className={`hv-micro${asset.is_watertight ? '' : ' hv-warn'}`}>
          {asset.is_watertight
            ? `ЗАГРУЖЕНА ${shortDate(asset.created_at, locale)}`
            : 'ТРЕБУЕТ ИСПРАВЛЕНИЯ'}
        </span>
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          disabled={!asset.is_watertight}
          onClick={onOrder}
        >
          Заказать
        </button>
      </div>
    </article>
  )
}

/**
 * A schematic block in the model's own proportions.
 *
 * Not a render of the mesh. The farm holds no thumbnails, and generating them
 * would mean rasterising every upload on a server that has printers to drive —
 * so this draws the *measured bounding box* instead, which is a fact the card
 * already states in millimetres. Wide parts look wide and tall ones tall, and
 * nothing here pretends to show the geometry.
 *
 * A mesh that is not watertight is drawn broken, the way the kit draws it: the
 * dashes are the same statement as «НЕ ГЕРМЕТИЧНА» above them.
 */
function Wireframe({ asset }: { asset: ModelAsset }) {
  const w = Math.max(Number(asset.width_mm), 1)
  const d = Math.max(Number(asset.depth_mm), 1)
  const h = Math.max(Number(asset.height_mm), 1)
  const scale = 70 / Math.max(w, d, h)
  const halfW = Math.min((w * scale) / 2, 70)
  const halfD = Math.min((d * scale) / 2, 45)
  const rise = Math.min(h * scale, 78)

  // Isometric-ish: the footprint is a rhombus, the body its vertical extrusion.
  const cx = 100
  const cy = 108
  const top = `${cx},${cy - halfD - rise} ${cx + halfW},${cy - rise} ${cx},${cy - rise + halfD} ${cx - halfW},${cy - rise}`
  const base = `${cx},${cy - halfD} ${cx + halfW},${cy} ${cx},${cy + halfD} ${cx - halfW},${cy}`
  const broken = asset.is_watertight ? {} : { strokeDasharray: '4 4' }

  return (
    <svg viewBox="0 0 200 150" aria-hidden="true">
      {asset.is_watertight && <polygon data-face points={top} />}
      <polygon data-edge points={top} {...broken} />
      <polygon data-edge points={base} {...broken} />
      <path
        data-edge
        {...broken}
        d={`M${cx - halfW} ${cy} L${cx - halfW} ${cy - rise} M${cx + halfW} ${cy} L${cx + halfW} ${cy - rise} M${cx} ${cy + halfD} L${cx} ${cy + halfD - rise}`}
      />
    </svg>
  )
}
