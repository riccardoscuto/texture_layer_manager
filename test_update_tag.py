# -*- coding: utf-8 -*-
# Test: verifica che rebuild_node_tree aggiorni il materiale senza spostare layer.
# Seleziona un oggetto con materiale TLM (con almeno un layer Fill), poi esegui.

import bpy

obj = bpy.context.active_object
if not obj or not obj.active_material:
    print("\n[ERRORE] Seleziona un oggetto con un materiale TLM attivo")
else:
    mat = obj.active_material
    tlm = mat.tlm

    if len(tlm.layers) < 1:
        print("\n[ERRORE] Il materiale non ha layer TLM")
    else:
        base_layer = None
        for layer in tlm.layers:
            if layer.layer_type == "FILL":
                base_layer = layer
                break

        if not base_layer:
            base_layer = tlm.layers[len(tlm.layers) - 1]
            print("[WARN] Nessun layer FILL trovato, uso '%s'" % base_layer.name)

        print("\n" + "="*60)
        print("  TEST: rebuild_node_tree + update_tag")
        print("="*60)

        if base_layer.layer_type == "FILL":
            old_color = tuple(base_layer.fill_color)
            print("\nLayer test: '%s'" % base_layer.name)
            print("  Colore attuale: R=%.3f G=%.3f B=%.3f" % (old_color[0], old_color[1], old_color[2]))

            print("\n[STEP 1] Cambio colore base a ROSSO (1, 0, 0, 1)...")
            base_layer.fill_color = (1.0, 0.0, 0.0, 1.0)

            print("[STEP 2] Chiamo rebuild_node_tree()...")
            from texture_layer_manager import compositing
            compositing.rebuild_node_tree(mat)

            print("[STEP 3] rebuild completato.")
            print("  -> Guarda la viewport: l'oggetto dovrebbe essere ROSSO")
            print("  -> Se e' ancora del colore precedente, la fix NON funziona")

            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

            print("\n[STEP 4] Verifico depsgraph...")
            dg = bpy.context.evaluated_depsgraph_get()
            eval_mat = mat.evaluated_get(dg)
            if eval_mat and eval_mat.node_tree:
                fill_nodes = [n for n in eval_mat.node_tree.nodes
                              if n.name.startswith("TLM_fill_")]
                if fill_nodes:
                    fn = fill_nodes[0]
                    out_val = fn.outputs[0].default_value
                    r, g, b = out_val[0], out_val[1], out_val[2]
                    print("  Nodo fill nel depsgraph: R=%.3f G=%.3f B=%.3f" % (r, g, b))
                    if r > 0.9 and g < 0.1 and b < 0.1:
                        print("\n  [PASS] Il depsgraph ha il colore ROSSO aggiornato!")
                    else:
                        print("\n  [FAIL] Il depsgraph ha ancora il colore vecchio!")
                else:
                    print("  [WARN] Nessun nodo TLM_fill_ trovato nel depsgraph")
            else:
                print("  [WARN] Impossibile valutare il depsgraph")

            idx = list(tlm.layers).index(base_layer)
            print("\nPer ripristinare il colore originale:")
            print("  bpy.context.active_object.active_material.tlm.layers[%d].fill_color = %s" % (idx, str(old_color)))
        else:
            print("\n[STEP 1] Chiamo rebuild_node_tree()...")
            from texture_layer_manager import compositing
            compositing.rebuild_node_tree(mat)

            tlm_nodes = [n for n in mat.node_tree.nodes if n.name.startswith("TLM_")]
            print("  Nodi TLM dopo rebuild: %d" % len(tlm_nodes))

            if len(tlm_nodes) > 0:
                print("\n  [PASS] rebuild completato, nodi creati")
            else:
                print("\n  [FAIL] nessun nodo TLM creato!")

            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

        print("\n" + "="*60)
