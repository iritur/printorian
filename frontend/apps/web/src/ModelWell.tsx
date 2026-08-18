import { useRef } from 'react'

import { translate, translateError } from '@printorian/ui'
import type { Locale } from '@printorian/ui'

import { ModelViewer } from './ModelViewer'

/**
 * The kit's model well: the geometry, with what the farm measured pinned to its
 * four corners, and the row of actions under it.
 *
 * The kit draws an isometric wireframe here with a comment calling it "a
 * placeholder for the WebGL canvas". This is that canvas — `ModelViewer`, the same
 * one the catalogue popup uses, on the bytes the customer just uploaded.
 *
 * Every annotation is measured. Nothing in this component is estimated or filled
 * in: an unclosed mesh has no volume, so the server refuses to quote it, and
 * reaching here at all means the numbers are real.
 */

export interface ModelWellProps {
  locale: Locale
  file: File | null
  /** Blob URL of the local file, so the viewer reads it without a round trip. */
  previewUrl: string | null
  measurements: {
    triangle_count: number
    volume_cm3: string
    bounding_box_mm: { x: string; y: string; z: string }
    mesh_warnings: string[]
  } | null
  /** Which of the four steps the customer is on, for «ШАГ 1 / 4». */
  step: number
  steps: number
  onFile: (file: File | null) => void
  onFromCatalog: () => void
}

export function ModelWell({
  locale,
  file,
  previewUrl,
  measurements,
  step,
  steps,
  onFile,
  onFromCatalog,
}: ModelWellProps) {
  const picker = useRef<HTMLInputElement | null>(null)
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  /*
    Megabytes only once there is a megabyte to show. The kit's example is «8.4 МБ»,
    but a small part rounds to «0.0 МБ», which reads as an empty file rather than a
    tidy one.
  */
  const size = !file
    ? '—'
    : file.size >= 1024 * 1024
      ? `${(file.size / 1024 / 1024).toFixed(1)} МБ`
      : `${Math.max(1, Math.round(file.size / 1024))} КБ`

  return (
    <>
      <section className="hv-frame hv-frame--wide" style={{ padding: 'var(--hv-2)' }}>
        <div className="hv-view" style={{ minHeight: 300 }}>
          {measurements && (
            <div className="hv-view__pin hv-view__pin--tl hv-stack hv-stack--2">
              <div className="hv-annot">
                <span>
                  <span className="hv-annot__k">Объём</span>
                  <span className="hv-annot__v">
                    {Number(measurements.volume_cm3).toFixed(2)} см³
                    {/*
                      The kit prints «герметично» beside the volume. It is only
                      true when the mesh closed — and an unclosed mesh has no
                      defined volume at all, which is why the server refuses to
                      quote one. Reaching here means it did close.
                    */}
                    {' · герметично'}
                  </span>
                </span>
              </div>
              <div className="hv-annot">
                <span>
                  <span className="hv-annot__k">Габарит</span>
                  <span className="hv-annot__v">
                    {Number(measurements.bounding_box_mm.x).toFixed(1)} ×{' '}
                    {Number(measurements.bounding_box_mm.y).toFixed(1)} ×{' '}
                    {Number(measurements.bounding_box_mm.z).toFixed(1)} мм
                  </span>
                </span>
              </div>
            </div>
          )}

          {measurements && (
            <div className="hv-view__pin hv-view__pin--tr hv-stack hv-stack--2">
              <div className="hv-annot">
                <span>
                  <span className="hv-annot__k">Треугольников</span>
                  <span className="hv-annot__v">
                    {measurements.triangle_count.toLocaleString(locale)}
                  </span>
                </span>
              </div>
              {/*
                One annotation per warning, toned `warn` as the kit does for thin
                walls. Codes, not prose, cross the wire (ADR-0012), so the wording
                is chosen here.
              */}
              {measurements.mesh_warnings.map((code) => (
                <div className="hv-annot hv-warn" key={code}>
                  <span>
                    <span className="hv-annot__k">Замечание</span>
                    <span className="hv-annot__v">{translateError(locale, { code })}</span>
                  </span>
                </div>
              ))}
            </div>
          )}

          <ModelViewer url={previewUrl} angle="iso" />

          <div className="hv-view__pin hv-view__pin--bl">
            <span className="hv-micro">
              {file ? `${file.name.toUpperCase()} :: ${size} :: ЗАГРУЖЕНО` : t('configurator.upload_prompt')}
            </span>
          </div>
          <div className="hv-view__pin hv-view__pin--br">
            <span className="hv-micro">ISO · 1:1 · СЕТКА 10 ММ</span>
          </div>
        </div>
        <div className="hv-ruler" style={{ marginTop: 'var(--hv-2)' }} />
      </section>

      <div className="hv-row">
        {/*
          The file input itself is hidden and driven by the kit's button. A native
          input cannot be styled into a Harvester button, and the label the kit
          shows — «Загрузить другую модель» — is not what a browser puts on one.
        */}
        <input
          ref={picker}
          type="file"
          accept=".stl"
          aria-label="STL"
          style={{ display: 'none' }}
          onChange={(event) => onFile(event.target.files?.[0] ?? null)}
        />
        <button className="hv-btn" type="button" onClick={() => picker.current?.click()}>
          {t('configurator.another_model')}
        </button>
        <button className="hv-btn" type="button" onClick={onFromCatalog}>
          {t('configurator.from_catalog')}
        </button>
        <span className="hv-spacer" />
        <span className="hv-micro">
          {t('configurator.step')} {step} / {steps}
        </span>
      </div>
    </>
  )
}
