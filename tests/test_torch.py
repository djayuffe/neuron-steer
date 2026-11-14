import unittest

try:
    import torch
except ImportError:
    torch = None
from neuron_steer.steering import install_steering


@unittest.skipIf(torch is None, "optional torch runtime not installed")
class RealTensorTests(unittest.TestCase):
    def test_additive_math_and_removal_with_real_tensors(self):
        class Block(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.mlp = torch.nn.Module()
                self.mlp.down_proj = torch.nn.Linear(8, 4, bias=False)
                with torch.no_grad():
                    self.mlp.down_proj.weight.fill_(1)

            def forward(self, x):
                return self.mlp.down_proj(x)

        class Model(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.model = torch.nn.Module()
                self.model.layers = torch.nn.ModuleList([Block()])

            def forward(self, input_ids):
                return self.model.layers[0](input_ids)

        model = Model().eval()
        encoded = {"input_ids": torch.ones(1, 3, 8)}
        before = model(**encoded).detach().clone()
        report, handles = install_steering(
            model, encoded, [dict(layer=0, neuron=2, dose=1)], torch
        )
        try:
            after = model(**encoded)
            self.assertTrue(torch.equal(after, before + 8))
            self.assertEqual(report["neurons"][0]["scale"], 8)
        finally:
            for handle in handles:
                handle.remove()
        self.assertTrue(torch.equal(model(**encoded), before))
