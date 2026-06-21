import sys
import traceback
try:
    from fusion.fusion_trainer import FusionTrainer
    import logging
    logging.basicConfig(level=logging.INFO, force=True,
                        format="%(levelname)s:%(name)s:%(message)s",
                        stream=sys.stdout)
    trainer = FusionTrainer(limit=None)
    w = trainer.train_global(class_weight={0: 1.0, 1: 3.0, 2: 1.0})
    if w:
        print(f"OK: accuracy={w.accuracy:.4f}, samples={w.sample_count}")
        path = w.save()
        print(f"Saved: {path}")
    else:
        print("FAIL: no weights returned")
except Exception as e:
    traceback.print_exc()
    print(f"ERROR: {e}")
