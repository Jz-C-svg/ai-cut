import unittest
from build_kinetic_text_dataset import events_for_video,validate

def frame(t,text='花字',x=0):
    block={'text':text,'confidence':.9,'polygon':[[0,0],[10,0],[10,10],[0,10]],'bbox_normalized':[x,.1,x+.1,.2],'relative_height':.1,'vertical_zone':'upper','large_text_candidate':True,'region_palette_includes_background':[]}
    return {'time':t,'frame':'test.jpg','frame_sha256':'framehash','text_blocks':[block] if text else []}
SOURCE={'source':'test.mp4','source_sha256':'sourcehash'}
class Tests(unittest.TestCase):
    def test_adjacent_observations_not_exact_duration(self):
        r=events_for_video(SOURCE,[frame(0),frame(5)],6);validate(r);self.assertEqual(len(r),1);self.assertEqual(len(r[0]['observations']),2);self.assertIsNone(r[0]['timing']['exact_end']);self.assertFalse(r[0]['timing']['continuous_presence_confirmed'])
    def test_absence_splits_reappearance(self):
        r=events_for_video(SOURCE,[frame(0),frame(2,''),frame(4)],6);self.assertEqual(len(r),2);self.assertEqual(r[0]['timing']['next_sample_time'],2)
    def test_changed_text_and_position_not_merged(self):
        r=events_for_video(SOURCE,[frame(0),frame(1,'别的字'),frame(2,'别的字',.3)],3);self.assertEqual(len(r),3);validate(r)
    def test_single_sample_is_not_zero_display_duration(self):
        r=events_for_video(SOURCE,[frame(1)],3)[0];self.assertEqual(r['timing']['observed_span_seconds'],0);self.assertIsNone(r['timing']['exact_start']);self.assertFalse(r['review']['eligible_as_gold_training_label'])
if __name__=='__main__':unittest.main()
