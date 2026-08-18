import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/**
 * The catalogue's 3D view.
 *
 * three.js bundled, never from a CDN: the farm runs on its own LAN (ADR-0003),
 * where nothing external resolves. That is the same reason the kit's fonts are
 * self-hosted.
 *
 * Drawn to look like the rest of the system rather than like a product render.
 * The kit's rule is that contrast comes from the line and colour means machine
 * state, so this is a flat unlit surface with its edges drawn on top — an
 * engineering view, not a showroom one. No coloured light, no specular, nothing
 * that would make a model look like a status.
 */

export type ViewAngle = 'iso' | 'top' | 'front'

/** Where the camera sits for each preset, as a direction from the centre. */
const ANGLES: Record<ViewAngle, [number, number, number]> = {
  iso: [1, 0.8, 1],
  top: [0, 1, 0.0001], // not exactly straight down: an exact pole makes `up` ambiguous
  front: [0, 0, 1],
}

export interface ModelViewerProps {
  /** Where the mesh comes from. `null` renders the empty state. */
  url: string | null
  angle: ViewAngle
  /** Turntable rotation. Off by default — motion is not decoration here. */
  spin?: boolean
}

export function ModelViewer({ url, angle, spin = false }: ModelViewerProps) {
  const mount = useRef<HTMLDivElement | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  // The camera and controls outlive each load, so the angle buttons can move an
  // already-loaded model without re-fetching it.
  const camera = useRef<THREE.PerspectiveCamera | null>(null)
  const controls = useRef<OrbitControls | null>(null)
  const radius = useRef(120)

  useEffect(() => {
    const host = mount.current
    if (!host || !url) {
      setStatus(url ? 'loading' : 'idle')
      return
    }

    setStatus('loading')

    const scene = new THREE.Scene()
    const width = host.clientWidth || 480
    const height = host.clientHeight || 360

    const cam = new THREE.PerspectiveCamera(38, width / height, 0.1, 5000)
    camera.current = cam

    /**
     * WebGL is not guaranteed.
     *
     * A browser with it disabled, a driver blocklist, or too many live contexts
     * all make this throw — and none of them should cost the customer the rest of
     * the screen. The viewer reports itself unavailable and the page carries on
     * with the numbers, which are the part that actually prices the job.
     */
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setStatus('failed')
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(width, height)
    // Transparent, so the panel's own ground — graph paper under `control`,
    // plain under `public` — shows through instead of a second background.
    renderer.setClearAlpha(0)
    host.appendChild(renderer.domElement)

    const orbit = new OrbitControls(cam, renderer.domElement)
    orbit.enableDamping = true
    orbit.enablePan = false
    controls.current = orbit

    /**
     * Read the theme's own colours rather than hardcoding two palettes.
     *
     * Void and Paper are the same drawing at different values, and taking the
     * tokens from the document means the viewer follows a theme switch without
     * knowing the themes exist.
     */
    const token = (name: string, fallback: string) =>
      getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback

    const surface = new THREE.Color(token('--hv-bg-inset', '#16181d'))
    const line = new THREE.Color(token('--hv-text-dim', '#8b929d'))

    let frame = 0
    let disposed = false
    const loader = new STLLoader()

    loader.load(
      url,
      (geometry) => {
        if (disposed) return
        geometry.computeVertexNormals()
        geometry.center()

        const box = new THREE.Box3().setFromBufferAttribute(
          geometry.getAttribute('position') as THREE.BufferAttribute,
        )
        const size = box.getSize(new THREE.Vector3())
        radius.current = Math.max(size.x, size.y, size.z) || 100

        // `MeshBasicMaterial` — unlit on purpose. A lit surface implies a light
        // source and a material finish, neither of which this screen knows.
        const solid = new THREE.Mesh(
          geometry,
          new THREE.MeshBasicMaterial({ color: surface, polygonOffset: true, polygonOffsetFactor: 1 }),
        )
        // The edges are the drawing; the fill is only there to occlude the ones
        // behind. `30` keeps the silhouette and the real corners while dropping
        // the tessellation of a curved face.
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geometry, 30),
          new THREE.LineBasicMaterial({ color: line }),
        )

        const model = new THREE.Group()
        model.add(solid, edges)
        // STL is Z-up, three.js is Y-up. Without this every part lies on its side.
        model.rotation.x = -Math.PI / 2
        scene.add(model)

        const place = (name: ViewAngle) => {
          const [x, y, z] = ANGLES[name]
          const distance = radius.current * 2.1
          cam.position.set(x, y, z).normalize().multiplyScalar(distance)
          cam.lookAt(0, 0, 0)
          orbit.target.set(0, 0, 0)
          orbit.update()
        }
        place(angle)
        setStatus('ready')

        const tick = () => {
          frame = requestAnimationFrame(tick)
          if (spin) model.rotation.z += 0.004
          orbit.update()
          renderer.render(scene, cam)
        }
        tick()
      },
      undefined,
      () => !disposed && setStatus('failed'),
    )

    const onResize = () => {
      const w = host.clientWidth || width
      const h = host.clientHeight || height
      cam.aspect = w / h
      cam.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
    const observer = new ResizeObserver(onResize)
    observer.observe(host)

    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      observer.disconnect()
      orbit.dispose()
      // WebGL contexts are a limited resource — a browser drops the oldest once
      // about sixteen are live, which on a grid of models means the first ones
      // silently go blank. Disposing on unmount is what keeps that from happening.
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments) {
          object.geometry.dispose()
          const material = object.material
          if (Array.isArray(material)) material.forEach((entry) => entry.dispose())
          else material.dispose()
        }
      })
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [url, spin, angle])

  // Angle changes move the existing camera rather than reloading the mesh.
  useEffect(() => {
    const cam = camera.current
    const orbit = controls.current
    if (!cam || !orbit || status !== 'ready') return
    const [x, y, z] = ANGLES[angle]
    cam.position.set(x, y, z).normalize().multiplyScalar(radius.current * 2.1)
    cam.lookAt(0, 0, 0)
    orbit.target.set(0, 0, 0)
    orbit.update()
  }, [angle, status])

  return (
    <div className="hv-viewer" ref={mount} data-status={status}>
      {status === 'loading' && <span className="hv-viewer__note">ЗАГРУЗКА ГЕОМЕТРИИ…</span>}
      {status === 'failed' && <span className="hv-viewer__note">ГЕОМЕТРИЯ НЕДОСТУПНА</span>}
      {status === 'idle' && <span className="hv-viewer__note">НЕТ ФАЙЛА МОДЕЛИ</span>}
    </div>
  )
}
