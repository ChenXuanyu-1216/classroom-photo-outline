const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
function mapper(){const context={};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../js/book-source-map.js'),'utf8'),context);return context.ClassroomBookSources.thumbnailForPage;}
const thumb=(filename,src='data:image/jpeg;base64,photo')=>({filename,src});
test('accepted PDF without thumbnail never shifts a later photo to the wrong source page',()=>{
 const get=mapper(),pages=[{page_no:1,filename:'notes.pdf'},{page_no:2,filename:'photo.jpg'}],cache=[thumb('photo.jpg')];
 assert.equal(get(cache,pages[0],pages),null);assert.equal(get(cache,pages[1],pages),cache[0]);
});
test('partial rejection maps thumbnails to server filenames rather than pre-upload indexes',()=>{
 const get=mapper(),pages=[{page_no:1,filename:'accepted.jpg'}],cache=[thumb('rejected.gif'),thumb('accepted.jpg','data:image/jpeg;base64,accepted')];
 assert.equal(get(cache,pages[0],pages),cache[1]);
});
test('duplicate names, legacy index-only caches and malformed image URLs fall back to text sources',()=>{
 const get=mapper(),page={page_no:1,filename:'photo.jpg'};
 assert.equal(get([thumb('photo.jpg'),thumb('photo.jpg')],page,[page]),null);
 assert.equal(get([thumb('photo.jpg')],page,[page,{page_no:2,filename:'photo.jpg'}]),null);
 assert.equal(get([{page:1,src:'data:image/jpeg;base64,old'}],page,[page]),null);
 assert.equal(get([thumb('photo.jpg','https://external.example/image')],page,[page]),null);
});
