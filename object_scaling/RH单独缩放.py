#! python 3
# -*- coding: utf-8 -*-

import Rhino
import scriptcontext as sc

def ScaleEach():
    factor = 2.0
    if "SCALEFACTOR" in sc.sticky:
        factor = float(sc.sticky["SCALEFACTOR"])
    
    objrefs = []
    
    while True:
        go = Rhino.Input.Custom.GetObject()
        go.AcceptNumber(True, False)
        optFactor = Rhino.Input.Custom.OptionDouble(factor)
        go.AddOptionDouble("Scale", optFactor)
        
        get_rc = go.GetMultiple(1, 0)

        if go.CommandResult() != Rhino.Commands.Result.Success:
            return go.CommandResult()
            
        if get_rc == Rhino.Input.GetResult.Object:
            for n in range(go.ObjectCount):
                objrefs.append(go.Object(n))
            break
            
        elif get_rc == Rhino.Input.GetResult.Number:
            factor = go.Number()
            sc.sticky["SCALEFACTOR"] = factor
            
        elif get_rc == Rhino.Input.GetResult.Option:
            factor = optFactor.CurrentValue
            sc.sticky["SCALEFACTOR"] = factor
            continue
    
    sc.sticky["SCALEFACTOR"] = factor
    if not objrefs:
        return
    
    sc.doc.Views.RedrawEnabled = False
    
    for objref in objrefs:
        obj_id = objref.ObjectId
        rhino_obj = sc.doc.Objects.Find(obj_id)
        if not rhino_obj or not rhino_obj.Geometry:
            continue
        
        bbox = rhino_obj.Geometry.GetBoundingBox(True)
        center = bbox.Center
        
        xform = Rhino.Geometry.Transform.Scale(center, factor)
        sc.doc.Objects.Transform(obj_id, xform, True)
    
    sc.doc.Views.RedrawEnabled = True
    sc.doc.Views.Redraw()
    
    return Rhino.Commands.Result.Success

if __name__ == "__main__":
    ScaleEach()
