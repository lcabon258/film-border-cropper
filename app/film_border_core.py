"""Film Border Cropper, Phase 1 v3 experimental: robust 35-mm inner-frame detection.

Requirements: Python >=3.10, opencv-python, numpy.
No training on reference corner coordinates is performed.

Use: python film_border_core_v3.py image1.jpeg image2.tif --out results --inset-px 0
Notes: this is an experimental detector, not the final V1 app. Phase 1 does
not preserve all image metadata or implement the web UI.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import cv2
import numpy as np

SIDES = ('left', 'right', 'top', 'bottom')


def load_image(path: Path) -> np.ndarray:
    # imdecode allows non-ASCII Windows filenames. IMREAD_UNCHANGED preserves
    # grayscale/RGB 8/16-bit pixel types but does not apply EXIF orientation.
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f'Unsupported or unreadable image: {path}')
    return image


def save_image(path: Path, image: np.ndarray) -> None:
    ext = path.suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        raise ValueError(f'Unsupported output: {ext}')
    ok, buf = cv2.imencode(ext, image)
    if not ok:
        raise ValueError(f'Cannot encode {path}')
    buf.tofile(str(path))


def grayscale_float(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 4:
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if gray.dtype == np.uint8:
        return gray.astype(np.float32)
    # For detection only, map 16-bit and floating image ranges to 0..255.
    p0, p1 = np.percentile(gray, [0.2, 99.8])
    return np.clip((gray.astype(np.float32) - p0) * (255.0 / max(p1 - p0, 1.0)), 0, 255)


def robust_line(x: np.ndarray, y: np.ndarray, weights: np.ndarray):
    """Fit position = slope*along + intercept; reject outliers iteratively."""
    if len(x) < 16:
        raise ValueError('Insufficient boundary observations')
    mask = np.ones(len(x), dtype=bool)
    for _ in range(6):
        if np.sum(mask) < 16:
            raise ValueError('Too few consistent edge observations')
        slope, intercept = np.polyfit(x[mask], y[mask], 1,
                                      w=np.sqrt(np.maximum(weights[mask], 0.1)))
        residual = y - (slope*x + intercept)
        mad = np.median(np.abs(residual[mask] - np.median(residual[mask])))
        tolerance = max(2.5, 3.0 * 1.4826 * mad)
        new_mask = np.abs(residual) < tolerance
        if np.array_equal(mask, new_mask):
            break
        mask = new_mask
    slope, intercept = np.polyfit(x[mask], y[mask], 1,
                                  w=np.sqrt(np.maximum(weights[mask], 0.1)))
    rms = float(np.sqrt(np.mean((y[mask] - (slope*x[mask] + intercept))**2)))
    return float(slope), float(intercept), rms, int(np.sum(mask))


def find_edge(gray: np.ndarray, side: str, _allow_gray: bool = False) -> dict:
    """Search slanted dark-to-picture boundaries, including near-image edges.

    A clipped gradient vote prevents a few bright scene edges dominating;
    exterior darkness and sustained contrast reject internal object lines.
    No image identifiers or annotation coordinates enter detection.
    """
    h, w = gray.shape
    vertical = side in ('left', 'right')
    perpendicular, along = (w,h) if vertical else (h,w)
    scan_coords = np.linspace(.12*along, .88*along, 160).astype(int)
    upper = max(16,int(perpendicular*.10))
    # Include enough pixels for positive/negative context around each candidate.
    scans=[]
    for coord in scan_coords:
        line = gray[coord,:] if vertical else gray[:,coord]
        if side in ('right','bottom'): line=line[::-1]
        scans.append(cv2.GaussianBlur(line[None,:].copy(),(0,0),2).ravel()[:upper+32])
    scans=np.asarray(scans)
    border_level=float(np.median(scans[:,:4]))
    dark_limit=min(110.0,max(55.0,border_level+30.0)) if _allow_gray else 55.0
    gradient=np.zeros_like(scans)
    gradient[:,4:-4]=scans[:,8:]-scans[:,:-8]
    # Small parallel offsets tolerate irregular film edges, without moving into
    # distant object edges. Context is sampled on both sides of the edge.
    response=gradient.copy()
    response[:,2:-2]=np.maximum.reduce([gradient[:,i:gradient.shape[1]-4+i] for i in range(5)])
    intercepts=np.arange(4,upper-12,2)
    slopes=np.linspace(-.04,.04,81)
    centered=scan_coords-along*.5
    rr=np.arange(len(scan_coords))[:,None]
    candidates=[]
    for slope in slopes:
        depths=np.rint(intercepts[None,:]+slope*centered[:,None]).astype(int)
        valid=(depths>=4)&(depths<upper-12)
        d=np.clip(depths,4,upper-13)
        g=response[rr,d]
        outside=(scans[rr,np.maximum(d-12,0)]+scans[rr,np.maximum(d-7,0)])/2
        inside=(scans[rr,d+7]+scans[rr,d+12])/2
        contrast=inside-outside
        visibility=(g>4)&(contrast>3)&valid
        darkness=np.clip((dark_limit-outside)/max(35.0,dark_limit-border_level),0,1)
        vote=np.clip(g/8,0,1)*np.clip(contrast/6,0,1)*darkness*valid
        score=vote.mean(axis=0)
        # Penalize an assumed border that contains a brighter band farther
        # outside. Film perforation glints affect a minority of scans only.
        far=scans[rr,np.maximum(d-40,0)]
        score*=1-.7*np.mean((far>dark_limit)&valid,axis=0)
        for ix in np.argsort(score)[-4:]:
            candidates.append((float(score[ix]),float(slope),int(intercepts[ix]),float(visibility[:,ix].mean())))
    candidates.sort(reverse=True)
    score,slope,intercept,support0=candidates[0]
    depth=np.rint(intercept+slope*centered).astype(int)
    # Refine each observed transition within a narrow normal band.
    local=np.clip(depth[:,None]+np.arange(-5,6)[None,:],4,upper-13)
    peaks=np.argmax(gradient[np.arange(160)[:,None],local],axis=1)
    positions=local[np.arange(160),peaks]
    strengths=gradient[np.arange(160),positions]
    exterior=scans[np.arange(160),np.maximum(positions-10,0)]
    interior=scans[np.arange(160),positions+10]
    valid=(strengths>4)&((interior-exterior)>3)&(exterior<dark_limit)
    if valid.sum()<32 or score<.12:
        if not _allow_gray and border_level>40:
            return find_edge(gray,side,_allow_gray=True)
        raise ValueError(f'{side}: insufficient coherent dark-to-picture boundary')
    fitted_slope,fitted_intercept,rms,inliers=robust_line(scan_coords[valid].astype(float),positions[valid].astype(float),np.minimum(strengths[valid],30))
    support=inliers/160
    strength=float(np.mean(np.minimum(strengths[valid],40)))
    if support<.25 or strength<5.5 or rms>6:
        raise ValueError(f'{side}: weak or inconsistent boundary')
    if side in ('right','bottom'):
        fitted_slope=-fitted_slope
        fitted_intercept=perpendicular-1-fitted_intercept
    return {'slope':fitted_slope,'intercept':fitted_intercept,'rms_px':rms,
            'inliers':inliers,'scanlines':160,'support':round(support,4),
            'anchor_depth_px':intercept,'mean_peak_gradient':round(strength,2),
            'border_level':round(border_level,2),'dark_limit':round(dark_limit,2),
            'candidate_score':round(score,4),'candidate_count':len(candidates),
            'method':'slanted_dark_exterior_consensus'}


def _intersect(vertical: dict, horizontal: dict) -> list[float]:
    a, b = vertical['slope'], vertical['intercept']
    c, d = horizontal['slope'], horizontal['intercept']
    if abs(1 - c*a) < 0.2:
        raise ValueError('Unreasonable frame intersection')
    y = (c*b + d) / (1 - c*a)
    return [a*y + b, y]


def inset_quad(corners: np.ndarray, margin_px: float) -> np.ndarray:
    """Offset all four fitted boundary lines inward by margin_px.

    For each edge, derive an inward-facing normal, shift the line, and
    intersect adjacent lines. Zero inset is an exact no-op.
    """
    if margin_px < 0:
        raise ValueError('inset must be >= 0')
    if margin_px == 0:
        return corners.copy()
    shifted = []
    center = corners.mean(axis=0)
    for i in range(4):
        p = corners[i]
        q = corners[(i+1) % 4]
        vec = q-p
        normal = np.array([-vec[1], vec[0]], dtype=float)
        normal /= max(np.linalg.norm(normal), 1e-8)
        if np.dot(center-p, normal) < 0:
            normal = -normal
        shifted.append((p + margin_px*normal, vec))
    result = []
    for i in range(4):
        p, u = shifted[(i-1) % 4]
        q, v = shifted[i]
        matrix = np.column_stack((u, -v))
        if abs(np.linalg.det(matrix)) < 1e-8:
            raise ValueError('Degenerate frame polygon')
        t = np.linalg.solve(matrix, q-p)[0]
        result.append(p+t*u)
    return np.array(result, dtype=np.float32)


def detect(image: np.ndarray, inset_px: float = 0) -> dict:
    gray = grayscale_float(image)
    h, w = gray.shape
    edges = {side: find_edge(gray, side) for side in SIDES}
    l, r, t, b = [edges[k] for k in SIDES]
    corners = np.array([_intersect(l,t), _intersect(r,t),
                        _intersect(r,b), _intersect(l,b)], dtype=np.float32)
    widths = [np.linalg.norm(corners[1]-corners[0]), np.linalg.norm(corners[2]-corners[3])]
    heights = [np.linalg.norm(corners[3]-corners[0]), np.linalg.norm(corners[2]-corners[1])]
    ratio = float(np.mean(widths) / np.mean(heights))
    if min(widths) < w*.55 or min(heights) < h*.55 or not 0.5 < ratio < 2.1:
        raise ValueError('Implausible 35mm film frame geometry')
    if np.any(corners[:,0] < -0.05*w) or np.any(corners[:,0] > 1.05*w) or \
       np.any(corners[:,1] < -0.05*h) or np.any(corners[:,1] > 1.05*h):
        raise ValueError('Frame corners out of bounds')
    # Heuristic only. Thresholds have NOT been calibrated on an independent dataset.
    rms = np.mean([e['rms_px'] for e in edges.values()])
    support = np.mean([e['support'] for e in edges.values()])
    strength = min(e['mean_peak_gradient'] for e in edges.values())
    heuristic = float(np.clip(0.4*np.exp(-rms/5.0) + 0.35*support +
                              0.25*min(1.0, strength/45.0), 0, 1))
    level = 'high' if heuristic >= .78 else ('medium' if heuristic >= .60 else 'low')
    quad = inset_quad(corners, inset_px)
    return {'corners': corners.tolist(), 'crop_corners': quad.tolist(),
            'edges': edges, 'aspect_ratio': round(ratio, 4),
            'inset_px': inset_px, 'confidence_heuristic': round(heuristic, 3),
            'confidence_level_provisional': level,
            'confidence_calibrated': False, 'core_version': 'phase1-v3-experimental',
            'automatic_crop_allowed': level == 'high' and all(e['support']>=.65 and e['rms_px']<=4 for e in edges.values()),
            'review_required': not (level == 'high' and all(e['support']>=.65 and e['rms_px']<=4 for e in edges.values()))}


def crop(image: np.ndarray, corners: list) -> np.ndarray:
    p = np.array(corners, np.float32)
    if p.shape != (4,2) or not np.all(np.isfinite(p)):
        raise ValueError('Expected four finite TL/TR/BR/BL corners')
    h,w=image.shape[:2]
    if np.any(p[:,0]<0) or np.any(p[:,0]>=w) or np.any(p[:,1]<0) or np.any(p[:,1]>=h):
        raise ValueError('Crop corners outside source image')
    if not cv2.isContourConvex(p.reshape(-1,1,2)) or abs(cv2.contourArea(p))<4:
        raise ValueError('Crop quadrilateral must be convex and nondegenerate')
    width = max(1, round((np.linalg.norm(p[1]-p[0])+np.linalg.norm(p[2]-p[3]))/2))
    height = max(1, round((np.linalg.norm(p[3]-p[0])+np.linalg.norm(p[2]-p[1]))/2))
    # Exact integer slicing if truly axis-aligned; preserve pixel values.
    if max(abs(p[0,1]-p[1,1]), abs(p[2,1]-p[3,1]),
           abs(p[0,0]-p[3,0]), abs(p[1,0]-p[2,0])) < 0.15:
        x0,y0 = np.ceil(p.min(axis=0)).astype(int)
        x1,y1 = np.floor(p.max(axis=0)).astype(int)
        return image[y0:y1, x0:x1].copy()
    dst = np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],np.float32)
    matrix = cv2.getPerspectiveTransform(p, dst)
    return cv2.warpPerspective(image, matrix, (width,height), flags=cv2.INTER_CUBIC)


def draw_boundary(image: np.ndarray, detected: list, reference: list | None = None) -> np.ndarray:
    if image.dtype == np.uint16:
        preview = (image / 257).astype(np.uint8)
    elif image.dtype != np.uint8:
        preview = np.clip(image, 0, 255).astype(np.uint8)
    else:
        preview = image.copy()
    if preview.ndim == 2:
        preview = cv2.cvtColor(preview, cv2.COLOR_GRAY2BGR)
    cv2.polylines(preview, [np.rint(detected).astype(np.int32)], True, (0,215,255), 2, cv2.LINE_AA)
    if reference is not None:
        cv2.polylines(preview, [np.rint(reference).astype(np.int32)], True, (80,240,70), 2, cv2.LINE_AA)
    return preview


def process_file(path: Path, out: Path, inset_px: float = 0) -> dict:
    image = load_image(path)
    detection = detect(image, inset_px)
    cropped = crop(image, detection['crop_corners'])
    if detection['automatic_crop_allowed']:
        save_image(out/(path.stem+'_v3_crop.png'), cropped)
    save_image(out/(path.stem+'_v3_boundary.png'), draw_boundary(image, detection['crop_corners']))
    detection.update({'file': path.name, 'input_shape': list(image.shape),
                      'input_dtype': str(image.dtype), 'output_shape': list(cropped.shape),
                      'output_dtype': str(cropped.dtype)})
    return detection


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('images', nargs='+', type=Path)
    p.add_argument('--out', type=Path, default=Path('results'))
    p.add_argument('--inset-px', type=float, default=0.0)
    args=p.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    results=[]
    for path in args.images:
        try:
            result=process_file(path,args.out,args.inset_px)
            print(path.name, result['confidence_level_provisional'],result['confidence_heuristic'])
        except Exception as exc:
            result={'file':path.name,'error':str(exc)}
            print(path.name,'ERROR',exc)
        results.append(result)
    (args.out/'detections_v3.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    main()
