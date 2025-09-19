import argparse
import os
import trimesh
import pyproj
import requests
import json
import shutil
from urllib.parse import urljoin

def download_mesh_folder(base_url, filename, download_dir):
    """
    Download OBJ, MTL and texture files from a given base_url into download_dir.
    """
    os.makedirs(download_dir, exist_ok=True)

    # Download OBJ
    obj_url = urljoin(base_url + "/", filename)
    obj_path = os.path.join(download_dir, filename)
    download_file(obj_url, obj_path)

    # Download MTL (same name but .mtl)
    mtl_filename = filename.replace(".obj", ".mtl")
    mtl_url = urljoin(base_url + "/", mtl_filename)
    mtl_path = os.path.join(download_dir, mtl_filename)
    download_file(mtl_url, mtl_path)

    # Parse MTL for texture references
    textures = []
    with open(mtl_path, "r") as f:
        for line in f:
            if line.strip().startswith("map_Kd"):
                tex_name = line.split()[-1].strip()
                textures.append(tex_name)

    # Download textures
    for tex in textures:
        tex_url = urljoin(base_url + "/", tex)
        tex_path = os.path.join(download_dir, tex)
        download_file(tex_url, tex_path)

    return obj_path

def download_file(url, dest):
    print(f"⬇️ Downloading {url} -> {dest}")
    r = requests.get(url, stream=True)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)
    else:
        raise Exception(f"Failed to download {url}: {r.status_code}")

def main():
    parser = argparse.ArgumentParser(description="Transform OBJ mesh using offsets and CRS conversion with JSON input.")
    parser.add_argument("--input", required=True, help="Input folder path or base URL containing the OBJ/MTL/Textures")
    parser.add_argument("--output", required=True, help="Output folder for transformed OBJ")
    parser.add_argument("--json", required=True, help="Path to JSON file containing offsets and images data")
    parser.add_argument("--in_crs", required=True, help="Input CRS (e.g. EPSG:32631)")
    parser.add_argument("--out_crs", required=True, help="Output CRS (e.g. EPSG:2054)")
    parser.add_argument("--filename", default="odm_textured_model_geo.obj", help="OBJ filename (default: odm_textured_model_geo.obj)")
    args = parser.parse_args()
    
    print(f"🔍 Input: {args.input}")
    print(f"📁 Output: {args.output}")
    print(f"🗺️ Input CRS: {args.in_crs}, Output CRS: {args.out_crs}")
    print(f"📂 Filename: {args.filename}")
    print(f"📝 JSON Config: {args.json}")
    
    local_dir = None

    # If input looks like a URL, download locally first
    if args.input.startswith("http://") or args.input.startswith("https://"):
        local_dir = "./_downloaded_mesh"
        input_path = download_mesh_folder(args.input, args.filename, local_dir)
    else:
        input_path = os.path.join(args.input, args.filename)

    output_path = os.path.join(args.output, args.filename)
    print(f"➡️ Output will be saved to {output_path}")
    metadata_path = os.path.join(args.output, "mesh_metadata.json")
    print(f"➡️ Metadata will be saved to {metadata_path}")

    # Load the mesh scene
    scene = trimesh.load(input_path)
    print('input_path', input_path)

    # Use offset only if provided, else default to zero
     # Load JSON config
    # Load JSON config (string from frontend)
    try:
        config = json.loads(args.json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON string passed to --json: {e}")
    
    print('config', config)

    offsets = config.get("offset", [0, 0, 0])
    images = config.get("images", [])

    if len(offsets) != 3:
        raise ValueError("Offset must be an array of exactly 3 values [x, y, z]")

    offset_x, offset_y, offset_z = offsets
    
    print(f"Using offsets: x={offset_x}, y={offset_y}, z={offset_z}")

    # Define CRS transformer
    transformer = pyproj.Transformer.from_crs(args.in_crs, args.out_crs, always_xy=True)
    first_vertex = None
    
    print(f"Transforming from {args.in_crs} to {args.out_crs}")

    # Apply offset + CRS transformation
    for name, mesh in scene.geometry.items():
        new_vertices = []
        for x, y, z in mesh.vertices:
            # Add offset if given
            x_global = x + offset_x
            y_global = y + offset_y
            z_global = z + offset_z
            # Transform to new CRS
            new_x, new_y = transformer.transform(x_global, y_global)
            new_vertices.append([new_x, new_y, z_global])
        
        mesh.vertices = new_vertices
        first_vertex = new_vertices[0]
        
    for name, mesh in scene.geometry.items():
        offset_vertices = []
        for x, y, z in mesh.vertices:
            # Add offset first
            new_x = x - first_vertex[0]
            new_y = y - first_vertex[1]
            new_z = z
            offset_vertices.append([new_x, new_y, new_z])
        
        mesh.vertices = offset_vertices
        
     # Transform image points
    transformed_images = []
    for img in images:
        print('img', img)
        filename = img.get("filename")
        point = img.get("point")
        if point and len(point) == 3:
            px, py, pz = point
            # Apply same offset
            px_global = px
            py_global = py
            pz_global = pz
            # CRS transform
            new_px, new_py = transformer.transform(px_global, py_global)
            transformed_images.append({
                "filename": filename,
                "point": [new_px, new_py, pz_global]
            })
            print(f"Transformed point: {new_px}, {new_py}, {pz_global}")
        else:
            transformed_images.append(img)  # keep unchanged if invalid
    
    # Export transformed mesh
    os.makedirs(args.output, exist_ok=True)
    scene.export(output_path)
    print(f"✅ Transformed mesh saved to {output_path}")
    
    # 🔧 Fix material reference in OBJ
    with open(output_path, "r") as f:
        obj_data = f.read()

    obj_data = obj_data.replace("material.mtl", "odm_textured_model_geo.mtl")

    with open(output_path, "w") as f:
        f.write(obj_data)
    print("🔗 Updated OBJ to reference odm_textured_model_geo.mtl")
    
     # Save anchor point metadata
    if first_vertex:
        metadata = {
            "offset": [first_vertex[0], first_vertex[1], 0],
            "images": transformed_images
        }
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        print(f"📝 Metadata saved to {metadata_path}")

    # ✅ Copy original MTL (with map_Kd) instead of using Trimesh’s stripped version
    original_mtl = os.path.join(os.path.dirname(input_path), args.filename.replace(".obj", ".mtl"))
    new_mtl_path = os.path.join(args.output, "odm_textured_model_geo.mtl")
    if os.path.exists(original_mtl):
        shutil.copy(original_mtl, new_mtl_path)
        print(f"📂 Copied original MTL (with textures) to {new_mtl_path}")
    else:
        print("⚠️ Warning: Original MTL not found, OBJ may not display textures correctly.")

    # ✅ Copy textures (keep them alongside OBJ/MTL in output)
    for tex in os.listdir(os.path.dirname(input_path)):
        if tex.lower().endswith((".jpg", ".jpeg", ".png")):
            src = os.path.join(os.path.dirname(input_path), tex)
            dst = os.path.join(args.output, tex)
            if not os.path.exists(dst):
                shutil.copy(src, dst)
                print(f"🖼️ Copied texture {tex} to output folder")

    # ✅ Clean up downloaded folder if used
    if local_dir and os.path.exists(local_dir):
        shutil.rmtree(local_dir)
        print(f"🗑️ Cleaned up temporary folder {local_dir}")

if __name__ == "__main__":
    main()
