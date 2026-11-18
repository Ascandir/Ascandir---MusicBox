"""MusicBox: APG-Führung für ACE-Step 1.5 (SFT/Base) – so wie im Original-Code von ACE-Step.

Hintergrund: Die SFT-/Base-Modelle von ACE-Step werden im Original nicht mit normalem CFG geführt, sondern mit
„Adaptive Projected Guidance“ (acestep/models/common/apg_guidance.py: apg_forward mit norm_threshold=2.5,
MomentumBuffer(momentum=-0.75), eta=0, Norm/Projektion je Zeitschritt über die Kanäle, dims=[1]).
Normales CFG mit Stärke 7 übersteuert und verzerrt vor allem die Stimme.

Wird von MusicBox automatisch nach ComfyUI/custom_nodes kopiert.
"""
import torch


def _project(v0, v1):
    dtype = v0.dtype
    v0, v1 = v0.double(), v1.double()
    v1 = torch.nn.functional.normalize(v1, dim=[1])
    par = (v0 * v1).sum(dim=[1], keepdim=True) * v1
    return par.to(dtype), (v0 - par).to(dtype)


class MusicBoxAceAPG:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "norm_threshold": ("FLOAT", {"default": 2.5, "min": 0.0, "max": 50.0, "step": 0.1}),
            "momentum": ("FLOAT", {"default": -0.75, "min": -5.0, "max": 1.0, "step": 0.01}),
            "eta": ("FLOAT", {"default": 0.0, "min": -10.0, "max": 10.0, "step": 0.01}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "musicbox"

    def patch(self, model, norm_threshold, momentum, eta):
        state = {"avg": None, "prev": None}

        def cfg_fn(args):
            # ComfyUI gibt hier x - denoised = sigma * v  → auf Geschwindigkeit v zurückrechnen
            c, u, s, sigma = args["cond"], args["uncond"], args["cond_scale"], args["sigma"]
            sig = sigma.reshape([-1] + [1] * (c.ndim - 1)).to(c.dtype).clamp(min=1e-6)
            vc, vu = c / sig, u / sig
            cur = float(sigma.flatten()[0])
            if state["prev"] is not None and cur > state["prev"]:      # neuer Durchlauf → Gedächtnis leeren
                state["avg"] = None
            state["prev"] = cur
            diff = vc - vu
            if momentum != 0:
                state["avg"] = diff if state["avg"] is None else diff + momentum * state["avg"]
                diff = state["avg"]
            if norm_threshold > 0:
                n = diff.norm(p=2, dim=[1], keepdim=True)
                diff = diff * torch.minimum(torch.ones_like(n), norm_threshold / (n + 1e-8))
            par, orth = _project(diff, vc)
            v = vc + (s - 1) * (orth + eta * par)
            return v * sig

        m = model.clone()
        m.set_model_sampler_cfg_function(cfg_fn, disable_cfg1_optimization=False)
        return (m,)


NODE_CLASS_MAPPINGS = {"MusicBoxAceAPG": MusicBoxAceAPG}
NODE_DISPLAY_NAME_MAPPINGS = {"MusicBoxAceAPG": "MusicBox ACE-Step APG"}
